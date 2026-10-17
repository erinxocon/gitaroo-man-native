#!/usr/bin/python3
"""Tile PNG frames from a directory into one half-scale contact sheet."""
import glob
import sys

from PIL import Image


def main():
    frame_dir, out_path = sys.argv[1], sys.argv[2]
    cols = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    paths = sorted(glob.glob(f"{frame_dir}/frame_*.png"))
    images = [Image.open(p).convert("RGB") for p in paths]
    w, h = images[0].size
    tw, th = w // 2, h // 2
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (tw * cols, th * rows))
    for i, image in enumerate(images):
        sheet.paste(image.resize((tw, th)), ((i % cols) * tw, (i // cols) * th))
    sheet.save(out_path)
    print(f"{len(images)} frames -> {out_path} {sheet.size}")


if __name__ == "__main__":
    main()
