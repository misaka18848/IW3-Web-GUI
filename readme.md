# IW3 Web GUI
## 简介
一个凑合能用的IW3 Web GUI，如果你觉得这个项目帮到了你，欢迎在左上角点个star  
## 如何使用
1.参考[本教程](https://github.com/nagadomi/nunif/blob/master/windows_package/docs/README.md)安装nunif  
2.下载本仓库，放在nunif文件夹里  
这时你的文件目录结构应该像这样  
```
- nunif-windows/
    - nunif的一些文件夹
    - nunif的一些bat（比如iw3-gui.bat update.bat等）
    - iw3web/
    - iw3-cli.bat
```
3.安装python  
4.在项目文件夹里打开命令提示符，输入以下内容安装项目依赖
```cmd
pip install -r requirements.txt
```
5.在项目文件夹里打开config.py,修改上传文件夹（UPLOAD_FOLDER）和转换文件夹（CONVERTED_FOLDER）到你需要的地方，修改最大存储空间（MAX_STORAGE_SIZE）和最大文件大小（MAX_CONTENT_LENGTH）  
6.在项目文件夹里打开app.py，修改底部的port=8000修改成你需要的端口  
7.在项目文件夹里打开命令提示符，输入以下内容启动Web GUI
```cmd
python app.py
```
然后你就可以访问localhost:上面设置的端口来使用IW3 Web GUI了