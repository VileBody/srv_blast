"""Build an ordered user montage without library selection, looping or cropping."""
from __future__ import annotations
import math
from typing import Any


def apply_custom_sources(config: dict[str, Any], sources: list[dict[str, Any]], duration: float) -> None:
    if not sources or not math.isfinite(duration) or duration <= 0:
        raise ValueError('custom sources require a positive composition duration')
    width, height = int(config['main_comp_w']), int(config['main_comp_h'])
    cursor = 0.0
    layers = []
    for index, source in enumerate(sources):
        url = str(source['url'])
        sw, sh, length = int(source['width']), int(source['height']), float(source['duration'])
        if not url.startswith('s3://') or sw <= 0 or sh <= 0 or not math.isfinite(length) or length < 1:
            raise ValueError('invalid custom source metadata')
        if abs((sw/sh)/(width/height)-1) > 0.02:
            raise ValueError('custom source geometry does not match render preset')
        if cursor >= duration: break
        out = min(duration, cursor+length)
        # Unique filename remains stable between the manifest and the generated JSX.
        name = f'custom_source_{index:03d}.mp4'
        layers.append({'layer_id': f'custom_{index}', 'type': 'footage', 'name': name,
            'file_name': name, 'media_file_name': name, 'file_path': url, 'src_w': sw, 'src_h': sh,
            'fit_mode': 'contain', 'in_point': cursor, 'out_point': out, 'start_time': cursor,
            'enabled': True, 'audio_enabled': False, 'video_enabled': True, 'target_comp': 'Comp 1'})
        cursor = out
    if cursor < duration - 0.001:
        raise ValueError(f'custom sources too short: {cursor:.2f}s for {duration:.2f}s composition')
    config['layers'] = layers + [layer for layer in config['layers'] if layer.get('type') != 'footage']
    config['custom_sources'] = {'count': len(layers), 'duration': duration, 'order': 'uploaded'}
