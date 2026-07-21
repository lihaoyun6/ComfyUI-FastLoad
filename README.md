#
<p align="center">
<h1 align="center">ComfyUI-FastLoad</h1>
<h3 align="center">Boost ComfyUI's web UI loading with object caching and gzip compression</h3>
<img src="./banner.png"/>
</p>  

**[[📃中文版](./README_zh.md)]** 

## Installation
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/lihaoyun6/FastLoad.git
```
Or install it from the Manager.  

## Usage
You don't need to do anything.  
It will automatically performs GZ compression of page resources and cache `object_info` on the first access after each time ComfyUI server startup.  
You will experience a huge speed boost from your second access to ComfyUI.


## Credits
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI) @comfyanonymous  
