import os
import gzip
import time
import json
import uuid
import hashlib
import logging
import traceback
import mimetypes
import urllib.parse
import server
import folder_paths
from aiohttp import web
from aiohttp.web_response import StreamResponse

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

logger = logging.getLogger(__name__)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CURRENT_DIR, "config.json")

DEFAULT_CONFIG = {
    "enabled": True,
    "max_cache": True,
    "ext_cache": True
}

def load_config():
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                config.update(loaded)
        except Exception as e:
            logger.error(f"[FastLoad] Failed to read config.json: {e}")
    return config

def save_config(config):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        logger.error(f"[FastLoad] Failed to save config.json: {e}")
        
# 加载当前配置，并赋值到内存全局变量中
current_config = load_config()
ENABLE_FASTLOAD = current_config.get("enabled", True)
ENABLE_EXTENSIONS_CACHE = current_config.get("ext_cache", False)
ENABLE_EXPERIMENTAL_STATIC_CACHE = current_config.get("max_cache", False)

_API_CACHE_STORE = {}
_STATIC_FILE_CACHE = {}
_BOOT_SIGNATURE = uuid.uuid4().hex  # 重启即变的唯一签名
print(_BOOT_SIGNATURE)
EXTS = {".js"}
IGNORE = {".git", "__pycache__", "node_modules"}
TARGET_CACHE_ROUTES = {
    "/object_info", "/api/object_info"
}
ALLOWED_API_CACHE_PREFIXES = (
    "/api/global_subgraphs/", "/global_subgraphs/", "/api/pysssss/autocomplete"
)

COMPRESSIBLE_TYPES = {
    "text/plain", "text/css", "text/html",
    "text/javascript", "application/javascript", "application/x-javascript",
    "application/json", "application/xml", "image/svg+xml"
}

STATIC_EXTS = {".js", ".css", ".html", ".svg", ".json", ".xml", ".txt"}

def scan_dir(path, file_list):
    try:
        for entry in os.scandir(path):
            name = entry.name
            if name.startswith('.') or name in IGNORE:
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    scan_dir(entry.path, file_list)
                else:
                    ext = os.path.splitext(name)[1]
                    if ext in EXTS:
                        stat = entry.stat(follow_symlinks=False)
                        file_list.append(f"{entry.path}_{stat.st_mtime_ns}_{stat.st_size}")
            except OSError:
                continue
    except OSError:
        pass

def get_nodes_environment_hash():
    """仅在 /object_info 缓存未命中时被调用，用于检测模型和节点的物理更新"""
    hasher = hashlib.md5()
    hasher.update(_BOOT_SIGNATURE.encode())
    file_list = []
    
    try:
        custom_node_paths = folder_paths.get_folder_paths("custom_nodes")
    except Exception:
        custom_node_paths = []
        
    for custom_dir in custom_node_paths:
        if os.path.isdir(custom_dir):
            scan_dir(custom_dir, file_list)
            
    visited_dirs = set()
    for type_name, paths_tuple in folder_paths.folder_names_and_paths.items():
        for base_path in paths_tuple[0]:
            if not os.path.isdir(base_path):
                continue
            for root, dirs, _ in os.walk(base_path, followlinks=True):
                real_root = os.path.realpath(root)
                if real_root in visited_dirs:
                    dirs[:] = []
                    continue
                visited_dirs.add(real_root)
                try:
                    stat = os.stat(root)
                    file_list.append(f"dir_{root}_{stat.st_mtime_ns}")
                except OSError:
                    pass
            
    for item in sorted(file_list):
        hasher.update(item.encode())
        
    return hasher.hexdigest()


# ======================================================================
# 核心劫持 1：全局中间件 (静态资源极致 304 + 307 URL 规范化重定向)
# ======================================================================
@web.middleware
async def global_gzip_middleware(request: web.Request, handler):
    if not ENABLE_FASTLOAD:
        return await handler(request)
    
    global _STATIC_FILE_CACHE
    
    # 【激进缓存】：检测到是静态资源类型，直接比对启动 UUID，实现 0 毫秒零读盘 304
    if ENABLE_EXPERIMENTAL_STATIC_CACHE and request.method == "GET":
        path = request.path
        ext = os.path.splitext(path)[1].lower()
        
        is_static_file = ext in STATIC_EXTS and not path.startswith(("/api/", "/internal/"))
        is_allowed_api = path.startswith(ALLOWED_API_CACHE_PREFIXES)
        is_extension_asset = "/extensions/" in path or "custom_nodes" in path
        
        # 补全逻辑：如果启用了“解除插件缓存”，且该路径属于插件资源，则将其排除在静态缓存之外
        if not ENABLE_EXTENSIONS_CACHE and is_extension_asset:
            is_static_file = False
            #is_allowed_api = False
            
        if is_static_file or is_allowed_api:
            if request.headers.get("If-None-Match") == _BOOT_SIGNATURE:
                return web.Response(status=304)
            
    # 307 URL 参数规范化重定向 (合并图片重复请求)
    if request.path in ("/view", "/api/view") and request.method == "GET":
        query = request.rel_url.query
        clean_query = {}
        for key in ("filename", "subfolder", "type", "preview", "channel"):
            if key in query:
                clean_query[key] = query[key]
                
        if len(query) != len(clean_query):
            has_cache_buster = True
        else:
            has_cache_buster = False
            
        sorted_items = sorted(clean_query.items())
        if has_cache_buster or list(query.items()) != sorted_items or request.path == "/view":
            canonical_query_str = urllib.parse.urlencode(sorted_items)
            redirect_url = f"/api/view?{canonical_query_str}"
            return web.Response(status=307, headers={"Location": redirect_url})
        
    response = await handler(request)
    
    # 劫持 1：精准篡改补全词库的 no-store 行为，并注入 ETag 黄金标头
    if ENABLE_EXPERIMENTAL_STATIC_CACHE and ("comfyui-custom-scripts/js/autocompleter.js" in request.path.lower()):
        if isinstance(response, web.FileResponse):
            filepath = getattr(response, '_path', None)
            if filepath and os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    js_content = f.read()
                js_content = js_content.replace('"no-store"', '"no-cache"').replace("'no-store'", "'no-cache'")
                new_resp = web.Response(
                    text=js_content, 
                    content_type='application/javascript',
                    headers={'ETag': _BOOT_SIGNATURE, 'Cache-Control': 'no-cache'}
                )
                if "gzip" in request.headers.get("Accept-Encoding", "").lower():
                    new_resp.enable_compression()
                return new_resp
            
    # 劫持 2：处理所有其他的 FileResponse（将大体积静态文件内存缓存，并强制 GZIP 压缩）
    if request.method == "GET" and isinstance(response, web.FileResponse):
        skipCaching = False
        _filepath = getattr(response, '_path', None)
        if _filepath:
            filepath = str(_filepath)
            
            is_extension_asset = "/extensions/" in filepath or "custom_nodes" in filepath
            if not ENABLE_EXTENSIONS_CACHE and is_extension_asset:
                skipCaching = True
            
            if filepath and os.path.exists(filepath):
                try:
                    content_type, _ = mimetypes.guess_type(filepath)
                    if not content_type:
                        content_type = response.content_type or 'application/octet-stream'
            
                    if content_type and ';' in content_type:
                        content_type = content_type.split(';')[0].strip()
            
                    is_compressible = (content_type in COMPRESSIBLE_TYPES) or (filepath.lower().endswith(tuple(STATIC_EXTS)))
            
                    if is_compressible:
                        stat = os.stat(filepath)
                        mtime = stat.st_mtime
            
                        if ENABLE_EXPERIMENTAL_STATIC_CACHE and not skipCaching:
                            cache_entry = _STATIC_FILE_CACHE.get(filepath)
                            if cache_entry and cache_entry[0] == mtime:
                                gzipped_data, content_type = cache_entry[1], cache_entry[2]
                            else:
                                with open(filepath, 'rb') as f:
                                    raw_data = f.read()
                                gzipped_data = gzip.compress(raw_data, compresslevel=6)
                                _STATIC_FILE_CACHE[filepath] = (mtime, gzipped_data, content_type)
            
                            headers = {'ETag': _BOOT_SIGNATURE, 'Cache-Control': 'no-cache', 'Content-Encoding': 'gzip'}
            
                            if request.headers.get("If-None-Match") == _BOOT_SIGNATURE:
                                return web.Response(status=304)
                        else:
                            with open(filepath, 'rb') as f:
                                raw_data = f.read()
                            gzipped_data = gzip.compress(raw_data, compresslevel=6)
                            headers = {'Content-Encoding': 'gzip'}
            
                        new_resp = web.Response(body=gzipped_data, content_type=content_type, headers=headers)
                        return new_resp
                except Exception as e:
                    logger.error(f"[FastLoad] Dynamic gzip failed for static asset {filepath}: {e}")
                    
    # 劫持 3：默认的标准 StreamResponse 兜底逻辑
    if not isinstance(response, web.StreamResponse):
        return response
    
    if "Content-Encoding" in response.headers:
        return response
    
    is_compressible = (response.content_type in COMPRESSIBLE_TYPES) or (request.path.lower().endswith(tuple(STATIC_EXTS)))
    if is_compressible:
        accept_encoding = request.headers.get("Accept-Encoding", "").lower()
        if "gzip" in accept_encoding:
            if isinstance(response, web.Response) and getattr(response, "body", None) is not None:
                response.enable_compression()
                
    return response

# ======================================================================
# 核心劫持 2：底层响应准备 (注入 ETag 黄金缓存控制)
# ======================================================================
original_stream_prepare = StreamResponse.prepare

async def patched_stream_prepare(self, request):
    if not ENABLE_FASTLOAD:
        return await original_stream_prepare(self, request)
    
    try:
        # 给已经标准化后的黄金图片响应注入 5 秒强缓存 + ETag 校验
        if request.path in ("/view", "/api/view"):
            if "Cache-Control" not in self.headers:
                self.headers["Cache-Control"] = "public, max-age=5, must-revalidate"
        
        # 【激进缓存】：给所有文本类静态资源打上启动签名 UUID 和 no-cache 标签
        elif ENABLE_EXPERIMENTAL_STATIC_CACHE and self.content_type in COMPRESSIBLE_TYPES:
            self.headers["ETag"] = _BOOT_SIGNATURE
            self.headers["Cache-Control"] = "no-cache" # 允许浏览器缓存，但每次必须向服务器验证
                    
        elif "Content-Encoding" not in self.headers:
            content_type = getattr(self, 'content_type', '')
            if content_type in COMPRESSIBLE_TYPES:
                accept_encoding = request.headers.get("Accept-Encoding", "").lower()
                if "gzip" in accept_encoding:
                    self.enable_compression()
    except Exception:
        pass 
    
    return await original_stream_prepare(self, request)

StreamResponse.prepare = patched_stream_prepare
logger.info("[FastLoad] Ultimate Session-Based Static Cache successfully loaded!")

original_add_routes = server.PromptServer.add_routes

def patched_add_routes(self, *args, **kwargs):
    original_add_routes(self, *args, **kwargs)
    
    try:
        # 挂载全局中间件
        if global_gzip_middleware not in self.app.middlewares:
            self.app.middlewares.insert(0, global_gzip_middleware)
            logger.info("[FastLoad] Global Gzip & Redirect middleware injected!")

        patched_count = 0
        for route in self.app.router.routes():
            route_path = route.resource.canonical if route.resource else None
            
            if route_path in TARGET_CACHE_ROUTES and route.method == "GET":
                original_handler = route.handler
                
                def create_cached_handler(handler_opt, path_key):
                    async def cached_handler(request):
                        if not ENABLE_FASTLOAD:
                            return await handler_opt(request)
                                                
                        global _API_CACHE_STORE
                        start_time = time.time()
                        current_hash = get_nodes_environment_hash()
                        
                        accept_encoding = request.headers.get("Accept-Encoding", "").lower()
                        supports_gzip = "gzip" in accept_encoding
                        
                        if request.headers.get('If-None-Match') == current_hash:
                            logger.info(f"[FastLoad] Hit browser ETag for {path_key} ({(time.time() - start_time)*1000:.2f}ms)")
                            return web.Response(status=304)
                        
                        cache_entry = _API_CACHE_STORE.get(path_key)
                        if cache_entry and cache_entry["hash"] == current_hash:
                            if supports_gzip and cache_entry["gzip"] is not None:
                                logger.info(f"[FastLoad] Hit Gzip cache for {path_key} ({(time.time() - start_time)*1000:.2f}ms)")
                                return web.Response(
                                    body=cache_entry["gzip"],
                                    content_type='application/json',
                                    headers={'ETag': current_hash, 'Content-Encoding': 'gzip'}
                                )
                            elif cache_entry["raw"] is not None:
                                logger.info(f"[FastLoad] Hit RAW cache for {path_key} ({(time.time() - start_time)*1000:.2f}ms)")
                                return web.Response(
                                    body=cache_entry["raw"],
                                    content_type='application/json',
                                    headers={'ETag': current_hash}
                                )
                        
                        logger.warning(f"[FastLoad] Cache miss for {path_key}. Generating...")
                        response = await handler_opt(request)
                        
                        if response.status == 200:
                            raw_data = None
                            
                            # 1. 处理标准的内存 Response
                            if hasattr(response, 'body') and response.body is not None:
                                raw_data = response.body
                                if isinstance(raw_data, str):
                                    raw_data = raw_data.encode('utf-8')
                                    
                            # 2. 处理文件流 FileResponse (pysssss 等插件使用的方式)
                            elif isinstance(response, web.FileResponse):
                                filepath = getattr(response, '_path', None)
                                if filepath and os.path.exists(filepath):
                                    try:
                                        with open(filepath, 'rb') as f:
                                            raw_data = f.read()
                                    except Exception as e:
                                        logger.error(f"[FastLoad] Failed to read FileResponse path {filepath}: {e}")
                                        
                            # 3. 如果成功提取到数据，存入内存并压缩
                            if raw_data is not None:
                                gzipped_data = gzip.compress(raw_data, compresslevel=6)
                                content_type = getattr(response, 'content_type', 'application/json')
                                
                                _API_CACHE_STORE[path_key] = {
                                    "raw": raw_data,
                                    "gzip": gzipped_data,
                                    "hash": current_hash,
                                    "content_type": content_type # 动态保存正确的 Content-Type
                                }
                            
                            raw_mb = len(raw_data) / 1024 / 1024
                            zip_mb = len(gzipped_data) / 1024 / 1024
                            logger.info(f"[FastLoad] {path_key} cached! RAW: {raw_mb:.2f}MB -> GZIP: {zip_mb:.2f}MB")
                            
                            if supports_gzip:
                                return web.Response(
                                    body=gzipped_data,
                                    content_type='application/json',
                                    headers={'ETag': current_hash, 'Content-Encoding': 'gzip'}
                                )
                            else:
                                response.headers['ETag'] = current_hash
                                return response
                            
                        return response
                    return cached_handler
                
                route._handler = create_cached_handler(original_handler, route_path)
                patched_count += 1
                
        if patched_count > 0:
            logger.info(f"[FastLoad] Success! {patched_count} API routes have been hijacked!")
        else:
            logger.warning("[FastLoad] Unable to hijack any API routes!")
        logger.info(f"[FastLoad] Current ETag: {_BOOT_SIGNATURE}")
            
    except Exception as e:
        logger.error(f"[FastLoad] Error: {e}")
        traceback.print_exc()
        
server.PromptServer.add_routes = patched_add_routes

@server.PromptServer.instance.routes.post("/fastload/config")
async def update_config_api(request):
    global ENABLE_FASTLOAD, ENABLE_EXTENSIONS_CACHE, ENABLE_EXPERIMENTAL_STATIC_CACHE, current_config
    try:
        data = await request.json()
        
        if "enabled" in data:
            current_config["enabled"] = bool(data["enabled"])
            ENABLE_FASTLOAD = current_config["enabled"]
            
        if "max_cache" in data:
            current_config["max_cache"] = bool(data["max_cache"])
            ENABLE_EXPERIMENTAL_STATIC_CACHE = current_config["max_cache"]
            
        if "ext_cache" in data:
            current_config["ext_cache"] = bool(data["ext_cache"])
            ENABLE_EXTENSIONS_CACHE = current_config["ext_cache"]
            
        save_config(current_config)
        return web.json_response({"status": "success", "config": current_config})
    
    except Exception as e:
        logger.error(f"[FastLoad] Failed to update config: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)
    
WEB_DIRECTORY = "./web"