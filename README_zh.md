#
<p align="center">
<h1 align="center">ComfyUI-FastLoad</h1>
<h3 align="center">大幅加快 ComfyUI 网页端热加载速度并提供低带宽网络优化</h3>
<img src="./banner.png"/>
</p>  

**[[📃English](./README.md)]**     

## 安装
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/lihaoyun6/ComfyUI-FastLoad.git
```
或从节点管理器中安装.

## 使用方法
安装后无需任何操作. 此扩展会自动使用 Gzip 算法压缩所有页面资源, 并在 ComfyUI 服务器启动后的首次被访问时缓存 `object_info` 对象以提高后续的热加载速度.  

## 致谢
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) @comfyanonymous  
