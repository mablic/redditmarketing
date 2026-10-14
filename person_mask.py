"""Local person detection helper, run with ComfyUI's Torch-enabled Python."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
from ultralytics import YOLO


def run(args):
    image = Image.open(args.input).convert('RGB')
    image.thumbnail((1024, 1024))
    result = YOLO(str(args.model)).predict(image, classes=[0], conf=0.2, verbose=False, device='cpu')[0]
    boxes = result.boxes.xyxy.cpu().tolist() if result.boxes is not None else []
    if args.check:
        print(json.dumps({'person_count': len(boxes)}))
        return
    mask = Image.new('L', image.size, 0)
    drawing = ImageDraw.Draw(mask)
    for x0, y0, x1, y1 in boxes:
        drawing.rectangle((max(0, x0-12), max(0, y0-12), min(image.width, x1+12), min(image.height, y1+12)), fill=255)
    mask = mask.filter(ImageFilter.MaxFilter(17))
    rgba = image.convert('RGBA')
    rgba.putalpha(Image.eval(mask, lambda value: 255-value))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rgba.save(args.output)
    print(json.dumps({'person_count': len(boxes), 'mask_file': str(args.output)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    run(parser.parse_args())
