import { app } from "../../scripts/app.js";

const originalFetch = window.fetch;
const ImageSrcDescriptor = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
const originalSetSrc = ImageSrcDescriptor.set;
const originalGetSrc = ImageSrcDescriptor.get;
const originalGetAttribute = Element.prototype.getAttribute; // 保存原始 getAttribute

const inflightFetches = new Map();
const objectUrlCache = new Map();

// 1. 全局配置状态机
window.FastLoadConfig = {
    imageMergeEnabled: app.ui.settings.getSettingValue("FastLoad.ImageMerge") ?? false,
    
    setMergeEnabled(enabled) {
        this.imageMergeEnabled = !!enabled;
        
        if (!this.imageMergeEnabled) {
            for (const objectUrl of objectUrlCache.values()) {
                URL.revokeObjectURL(objectUrl);
            }
            objectUrlCache.clear();
            inflightFetches.clear();
        }
    }
};

function canonicalizeUrl(urlStr) {
    try {
        const url = new URL(urlStr, window.location.origin);
        if (url.pathname === '/view' || url.pathname === '/api/view') {
            const params = new URLSearchParams();
            for (const key of ["filename", "subfolder", "type", "preview", "channel"]) {
                if (url.searchParams.has(key)) {
                    params.set(key, url.searchParams.get(key));
                }
            }
            params.sort();
            return `/api/view?${params.toString()}`;
        }
    } catch (e) {}
    return null;
}

// 2. 劫持 fetch
window.fetch = async function (input, init) {
    if (!window.FastLoadConfig.imageMergeEnabled) {
        return originalFetch.apply(this, arguments);
    }
    
    let urlStr = typeof input === 'string' ? input : (input instanceof Request ? input.url : null);
    const canonical = urlStr ? canonicalizeUrl(urlStr) : null;
    
    if (canonical && (!init || init.method === 'GET' || !init.method)) {
        if (inflightFetches.has(canonical)) {
            const response = await inflightFetches.get(canonical);
            return response.clone(); 
        }
        
        const fetchPromise = (async () => {
            try {
                return await originalFetch(canonical, init);
            } catch (err) {
                inflightFetches.delete(canonical);
                throw err;
            }
        })();
        
        inflightFetches.set(canonical, fetchPromise);
        
        fetchPromise.finally(() => {
            setTimeout(() => {
                inflightFetches.delete(canonical);
            }, 1000);
        });
        
        const response = await fetchPromise;
        return response.clone();
    }
    
    return originalFetch.apply(this, arguments);
};

// 3. 对其他插件透明的虚拟化 Image src 劫持
Object.defineProperty(HTMLImageElement.prototype, 'src', {
    // 【透明化修改】：当其他插件读取 img.src 时，返回它设置的原始路径（如 ./view...），而不是内存中的 blob: 地址
    get() {
        if (this._originalSrc) {
            try {
                // 模拟原生行为：.src 属性查询时自动补全为绝对 URL
                return new URL(this._originalSrc, window.location.href).href;
            } catch (e) {
                return this._originalSrc;
            }
        }
        return originalGetSrc.call(this);
    },
    set(val) {
        // 保存原始写入路径，用于伪装
        this._originalSrc = val;
        
        if (!window.FastLoadConfig.imageMergeEnabled) {
            originalSetSrc.call(this, val);
            return;
        }
        
        const canonical = canonicalizeUrl(val);
        if (canonical) {
            if (objectUrlCache.has(canonical)) {
                originalSetSrc.call(this, objectUrlCache.get(canonical));
                return;
            }
            
            window.fetch(canonical)
            .then(res => res.blob())
            .then(blob => {
                if (!window.FastLoadConfig.imageMergeEnabled) {
                    originalSetSrc.call(this, val);
                    return;
                }
                const objectUrl = URL.createObjectURL(blob);
                objectUrlCache.set(canonical, objectUrl);
                originalSetSrc.call(this, objectUrl);
                
                if (objectUrlCache.size > 100) {
                    const firstKey = objectUrlCache.keys().next().value;
                    const oldUrl = objectUrlCache.get(firstKey);
                    URL.revokeObjectURL(oldUrl);
                    objectUrlCache.delete(firstKey);
                }
            })
            .catch(() => {
                originalSetSrc.call(this, val);
            });
            return;
        }
        originalSetSrc.call(this, val);
    }
});

// 【透明化修改】：劫持 getAttribute，当其他插件查询 img.getAttribute("src") 时，返回原始相对路径
Element.prototype.getAttribute = function (name) {
    if (name === "src" && this instanceof HTMLImageElement && this._originalSrc) {
        return this._originalSrc;
    }
    return originalGetAttribute.apply(this, arguments);
};

// 4. 暴露给 iframe 的桥接接口
window.FastLoadImageBridge = {
    async fetch(urlStr, init) {
        return await window.fetch(urlStr, init);
    },
    async getObjectUrl(val) {
        const canonical = canonicalizeUrl(val);
        if (!canonical || !window.FastLoadConfig.imageMergeEnabled) {
            return val;
        }
        if (objectUrlCache.has(canonical)) {
            return objectUrlCache.get(canonical);
        }
        const res = await window.fetch(canonical);
        const blob = await res.blob();
        const objectUrl = URL.createObjectURL(blob);
        objectUrlCache.set(canonical, objectUrl);
        return objectUrl;
    }
};