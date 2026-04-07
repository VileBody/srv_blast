import heapq
import os
from pathlib import Path

ROOT = Path(r"C:\ ")
ROOT = Path(str(ROOT).strip())  # -> C:\
TOP_N = 100

def iter_files(root: Path):
    stack = [str(root)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            st = e.stat(follow_symlinks=False)
                            yield e.path, st.st_size
                    except OSError:
                        continue
        except OSError:
            continue

def human_gb(n: int) -> str:
    return f"{n / (1024**3):.2f} GB"

def main():
    heap = []  # min-heap of (size, path)
    scanned = 0

    for path, size in iter_files(ROOT):
        scanned += 1
        if len(heap) < TOP_N:
            heapq.heappush(heap, (size, path))
        else:
            if size > heap[0][0]:
                heapq.heapreplace(heap, (size, path))

        if scanned % 50000 == 0:
            print(f"scanned files: {scanned}")

    top = sorted(heap, key=lambda x: x[0], reverse=True)

    print(f"\nScanned files total: {scanned}")
    print(f"Top {TOP_N} largest files on {ROOT}:\n")
    for i, (size, path) in enumerate(top, 1):
        print(f"{i:3d}. {human_gb(size):>10}  {path}")

if __name__ == "__main__":
    main()
