import math
from typing import Iterable, List, Tuple
import random

# yoinked from https://505e06b2.github.io/Image-to-Braille/
def pixels_to_char(pixels: Iterable[bool], monospace=False):
    if len(pixels) != 8:
        raise ValueError(f"Expected an array of 8 bools, got length {len(pixels)}")
    shifts = [0, 1, 2, 6, 3, 4, 5, 7]
    codepoint_offset = 0
    for idx, px in enumerate(pixels):
        codepoint_offset += int(px) << shifts[idx]
    
    if codepoint_offset == 0 and monospace:
        # pickles = [False] * 8
        # pickles[random.randint(0, 7)] = True
        # return pixels_to_char(pickles)
        codepoint_offset = 4
    return chr(0x2800 + codepoint_offset)

def interpolate_values(input_values, output_count):
    input_count = len(input_values)
    if input_count < 2:
        raise ValueError("Need at least two input values to interpolate.")
    if output_count < 2:
        raise ValueError("Need at least two output values to interpolate.")
    
    result = []
    for i in range(output_count):
        position = i * (input_count - 1) / (output_count - 1)
        left_index = int(position)
        right_index = min(left_index + 1, input_count - 1)
        fraction = position - left_index
        
        left_value = input_values[left_index]
        right_value = input_values[right_index]
        interpolated = left_value + (right_value - left_value) * fraction
        result.append(interpolated)
    
    return result

def interpolate_with_gap_handling(pairs, output_count, max_gap):
    """
    Interpolates (timestamp, value) pairs while respecting large time gaps.
    Inserts None for values in regions where the time gap is too large.

    Args:
        pairs (list of (float, float)): Input data as (timestamp, value).
        output_count (int): Total number of points to interpolate.
        max_gap (float): Maximum allowed time gap for interpolation.

    Returns:
        list of (float, Optional[float]): Interpolated (timestamp, value) pairs.
    """
    if len(pairs) < 2:
        raise ValueError("Need at least two data points.")

    # Full time span and output resolution
    start_time = pairs[0][0]
    end_time = pairs[-1][0]
    total_duration = end_time - start_time

    if total_duration <= 0:
        raise ValueError("Timestamps must be strictly increasing.")

    step = total_duration / (output_count - 1)
    output = []

    # Build lookup list
    for i in range(output_count):
        t = start_time + i * step

        # Find two input points surrounding t
        for j in range(len(pairs) - 1):
            t0, v0 = pairs[j]
            t1, v1 = pairs[j + 1]

            if t0 <= t <= t1:
                if (t1 - t0) > max_gap:
                    output.append((t, None))
                else:
                    fraction = (t - t0) / (t1 - t0)
                    v = v0 + (v1 - v0) * fraction
                    output.append((t, v))
                break
        else:
            # t is beyond the last known range
            output.append((t, None))

    return output

# Expects (timestamp, value) pairs
def simple_line_graph(pairs: Iterable[Tuple[float, float]], width=24, height=4, min_val=None, max_val=None, hard_min_val=None, hard_max_val=None, fill_type=0, max_gap=20, monospace=False):
    values = [x[1] for x in pairs]
    if max_val is None:
        max_val = max(values)
    if min_val is None:
        m = min(values)
        min_val = max_val - (max_val - m) * 1/(3.5/4)
    if hard_min_val is not None:
        min_val = max(hard_min_val, min_val)
    if hard_max_val is not None:
        max_val = min(hard_max_val, max_val)
    total_range = max_val - min_val
    if total_range <= 0:
        max_val = min_val + 1
        total_range = 1
    
    values_interpolated = interpolate_with_gap_handling(pairs, width, max_gap)
    columns = []
    for pair in values_interpolated:
        t, v = pair
        col = []
        if v is not None:
            a = (v - min_val) / total_range * height
            b = int(a)
            if fill_type == 0:
                for x in range(height):
                    col.append(x == b)
            elif fill_type == 1:
                for x in range(height):
                    col.append(x <= b)
            elif fill_type == 2:
                for x in range(height):
                    col.append(x >= b)
        else:
            for x in range(height):
                col.append(False)
        if len(col) % 4 > 0:
            col.extend([False] * (len(col) % 4))
        columns.append(list(reversed(col)))
    if len(columns) % 2 == 1:
        columns.append([False] * (height + (4 - height % 4)))
    
    out_chars = []
    for b in range(0,math.ceil(height/4)):
        b *= 4
        for a in range(0, len(columns), 2):
            col1 = columns[a][b:b+4]
            col2 = columns[a+1][b:b+4]
            out_chars.append(pixels_to_char(col1 + col2, monospace))
        out_chars.append("\n")
    out_str = ''.join(out_chars)
    return out_str.strip()

if __name__ == "__main__":
    test_data = []
    t = 0
    for x in range(20):
        test_data.append((t, math.sin(x)))
        t += random.randrange(8, 25)
    print(simple_line_graph(test_data, fill_type=1, height=8, width=100, monospace=False))