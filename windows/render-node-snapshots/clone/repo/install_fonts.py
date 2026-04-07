import os
import sys
import tempfile
import zipfile
import shutil

import requests  # pip install "requests[socks]"

# Прямой линк на public-файл Google Drive
GDRIVE_FILE_ID = "1hc86841dn50XXr7KdgzVf8Ole47PoG46"
FONT_URL = f"https://drive.google.com/uc?export=download&id={GDRIVE_FILE_ID}"


def download_via_proxy(url: str, dst_path: str) -> None:
    """
    Скачиваем файл через HTTP(S), поддерживаем SOCKS5-прокси из OUTBOUND_PROXY.

    ВАЖНО: verify=False -> проверка TLS-сертификата отключена.
    Это небезопасно в общем случае, но ок для dev-сервера без нормальных CA.
    """
    proxy =  "socks5h://1JRapBBa:ZFTpzXPh@172.120.176.193:64299"
    proxies = {}

    if proxy:
        print(f"Использую прокси для HTTP/HTTPS: {proxy}")
        proxies = {
            "http": proxy,
            "https": proxy,
        }
    else:
        print("OUTBOUND_PROXY не задан, качаю напрямую (без прокси).")

    try:
        with requests.get(
            url,
            stream=True,
            proxies=proxies,
            timeout=120,
            verify=False,  # вырубаем проверку сертификатов
        ) as resp:
            resp.raise_for_status()

            # На всякий случай можно проверить тип контента,
            # но Drive обычно отдаёт корректный application/zip.
            print(f"Content-Type: {resp.headers.get('Content-Type')}")

            with open(dst_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
    except requests.RequestException as e:
        print(f"Ошибка скачивания: {e}")
        sys.exit(1)


def main():
    if os.name != "nt":
        print("Этот скрипт рассчитан на Windows (os.name == 'nt').")
        sys.exit(1)

    windir = os.environ.get("WINDIR", r"C:\Windows")
    fonts_dir = os.path.join(windir, "Fonts")

    print("Временная директория...")
    temp_dir = tempfile.mkdtemp(prefix="point_fonts_")
    zip_path = os.path.join(temp_dir, "point.zip")

    print("Скачиваю шрифты Point с Google Drive...")
    download_via_proxy(FONT_URL, zip_path)

    print("Распаковываю архив...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(temp_dir)

    print("Ищу *.ttf / *.otf...")
    installed = 0
    for root, dirs, files in os.walk(temp_dir):
        for name in files:
            if not name.lower().endswith((".ttf", ".otf")):
                continue

            src = os.path.join(root, name)
            dst = os.path.join(fonts_dir, name)

            if not os.path.exists(dst):
                print(f"Устанавливаю {name} -> {dst}")
                shutil.copy2(src, dst)
                installed += 1
            else:
                print(f"Уже есть: {name}")

    print(f"Готово. Установлено {installed} файлов шрифтов в {fonts_dir}.")
    print("Перезапусти приложения / сеанс, чтобы шрифты появились в списке.")


if __name__ == "__main__":
    main()
