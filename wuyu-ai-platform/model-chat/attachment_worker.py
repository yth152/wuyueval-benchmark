"""Parse documents/images in a time-limited process without application secrets."""
import base64
import importlib.util
import io
import json
import os
import re
import sys
import warnings
from pathlib import Path

if os.name != 'nt':
    import resource
    # Keep the parser disposable even when an uploaded archive or document
    # triggers pathological behavior in a third-party decoder.  The parent
    # process also enforces a wall-clock timeout.
    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (35, 40))
    resource.setrlimit(resource.RLIMIT_FSIZE, (80 * 1024 * 1024, 80 * 1024 * 1024))

def parse(name, data):
    ext=Path(name).suffix.lower()
    if ext in {'.png','.jpg','.jpeg','.webp','.gif','.bmp'}:
        from PIL import Image, ImageOps
        Image.MAX_IMAGE_PIXELS=30_000_000
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as original:
            if original.format not in {'PNG','JPEG','WEBP','GIF','BMP'}: raise ValueError('不支持此图片格式。')
            animated=getattr(original,'n_frames',1)>1
            oriented=ImageOps.exif_transpose(original).convert('RGBA')
            frame=Image.new('RGB',oriented.size,'white')
            frame.paste(oriented,mask=oriented.getchannel('A'))
            width,height=frame.size
            frame.thumbnail((2048,2048))
            # Normalize to a standard JPEG profile accepted by the deployed
            # vision service, retaining resolution and removing source metadata.
            out=io.BytesIO();frame.save(out,format='JPEG',quality=90,optimize=False,progressive=False,subsampling=2)
        return {'kind':'image','image':base64.b64encode(out.getvalue()).decode(), 'width':width,'height':height,
                'model_image_encoding':'baseline-jpeg-v2',
                'warning':('动图仅发送首帧。' if animated else '') + ('图片长边已缩至 2048 像素。' if max(width,height)>2048 else '')}
    path=Path(__file__).resolve().parent.parent/'server/documents.py'
    spec=importlib.util.spec_from_file_location('document_parser',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    text,warning=module.extract(name,data)
    visible_text = re.sub(r'第 \d+ 页', '', text).strip() if ext=='.pdf' else text.strip()
    if not visible_text: raise ValueError('文档没有可读文字，请先进行 OCR 或将页面作为图片上传。')
    return {'kind':'document','text':text,'warning':warning}

if __name__=='__main__':
    try:
        result=parse(sys.argv[1],sys.stdin.buffer.read(20*1024*1024+1))
        sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False).encode('utf-8'))
    except Exception as exc:
        message=str(exc) if isinstance(exc,ValueError) else '文件解析失败，请确认文件未损坏、未加密，或另存为新格式。'
        sys.stdout.buffer.write(json.dumps({'error':message},ensure_ascii=False).encode('utf-8'));sys.exit(1)
