import os
import hashlib
import time
import uuid
import logging
import traceback
import gzip
import server
import folder_paths
from aiohttp import web
from aiohttp.web_response import StreamResponse

logger = logging.getLogger(__name__)

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
_API_CACHE_STORE = {}
_BOOT_SIGNATURE = uuid.uuid4().hex

EXTS = {".js", ".py"}
IGNORE = {".git", "__pycache__", "node_modules"}
TARGET_CACHE_ROUTES = {"/object_info", "/api/object_info"}
COMPRESSIBLE_TYPES = {
    "text/plain", "text/css", "text/html",
    "text/javascript", "application/javascript", "application/x-javascript",
    "application/json", "application/xml", "image/svg+xml",
}

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
    hasher = hashlib.md5()
    hasher.update(_BOOT_SIGNATURE.encode())
    
    try:
        custom_node_paths = folder_paths.get_folder_paths("custom_nodes")
    except Exception:
        custom_node_paths = []
        
    file_list = []
    for custom_dir in custom_node_paths:
        if os.path.isdir(custom_dir):
            scan_dir(custom_dir, file_list)
            
    for item in sorted(file_list):
        hasher.update(item.encode())
        
    return hasher.hexdigest()

original_stream_prepare = StreamResponse.prepare

async def patched_stream_prepare(self, request):
    try:
        if "Content-Encoding" not in self.headers:
            content_type = getattr(self, 'content_type', '')
            if content_type in COMPRESSIBLE_TYPES:
                accept_encoding = request.headers.get("Accept-Encoding", "").lower()
                if "gzip" in accept_encoding:
                    self.enable_compression()
    except Exception:
        pass 
    
    return await original_stream_prepare(self, request)

StreamResponse.prepare = patched_stream_prepare
logger.info("[FastLoad] Ultimate Gzip hijacker attached to aiohttp StreamResponse!")

original_add_routes = server.PromptServer.add_routes

def patched_add_routes(self, *args, **kwargs):
    original_add_routes(self, *args, **kwargs)
    
    try:
        patched_count = 0
        for route in self.app.router.routes():
            route_path = route.resource.canonical if route.resource else None
            
            if route_path in TARGET_CACHE_ROUTES and route.method == "GET":
                original_handler = route.handler
                
                def create_cached_handler(handler_opt, path_key):
                    async def cached_handler(request):
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
                            raw_data = response.body
                            gzipped_data = gzip.compress(raw_data, compresslevel=6)
                            
                            _API_CACHE_STORE[path_key] = {
                                "raw": raw_data,
                                "gzip": gzipped_data,
                                "hash": current_hash
                            }
                            
                            raw_kb = len(raw_data) / 1024
                            zip_kb = len(gzipped_data) / 1024
                            logger.info(f"[FastLoad] {path_key} cached! RAW: {raw_kb:.2f}KB -> GZIP: {zip_kb:.2f}KB")
                            
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
            
    except Exception as e:
        logger.error(f"[FastLoad] Error: {e}")
        traceback.print_exc()
        
server.PromptServer.add_routes = patched_add_routes