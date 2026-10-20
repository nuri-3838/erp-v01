"""TEST YARDIMCISI (test modülü DEĞİL): rclone'un drive_yedek'in kullandığı alt komutlarını (copyto / lsjson / deletefile) yerel bir klasörde taklit eder.
Uzak "erpdrive:ERP_YEDEK/x" → $FAKE_RCLONE_ROOT/ERP_YEDEK/x. Hata enjeksiyonu: $FAKE_RCLONE_FAIL dosyası varsa copyto hata verir; $FAKE_RCLONE_KESIK varsa dosya yarım yazılır."""
import json
import os
import shutil
import sys

kok = os.environ["FAKE_RCLONE_ROOT"]


def yerel(uzak):
    return os.path.join(kok, uzak.split(":", 1)[1])


args = [a for a in sys.argv[1:] if not a.startswith("--")]
if "--retries" in sys.argv:                                   # --retries N: N'i de at
    i = sys.argv.index("--retries")
    args = [a for a in sys.argv[1:i] if not a.startswith("--")] + [a for a in sys.argv[i + 2:] if not a.startswith("--")]
komut = args[0]
if komut == "copyto":
    if os.path.exists(os.environ.get("FAKE_RCLONE_FAIL", "/nonexistent")):
        sys.stderr.write("fake rclone: kasıtlı hata\n")
        sys.exit(1)
    hedef = yerel(args[2])
    os.makedirs(os.path.dirname(hedef), exist_ok=True)
    if os.path.exists(os.environ.get("FAKE_RCLONE_KESIK", "/nonexistent")):
        with open(args[1], "rb") as f, open(hedef, "wb") as g:
            g.write(f.read()[:10])
    else:
        shutil.copyfile(args[1], hedef)
elif komut == "lsjson":
    hedef = yerel(args[1])
    if os.path.isdir(hedef):
        liste = [{"Name": n, "Size": os.path.getsize(os.path.join(hedef, n)), "IsDir": False} for n in sorted(os.listdir(hedef)) if os.path.isfile(os.path.join(hedef, n))]
    elif os.path.isfile(hedef):
        liste = [{"Name": os.path.basename(hedef), "Size": os.path.getsize(hedef), "IsDir": False}]
    else:
        liste = []
    print(json.dumps(liste))
elif komut == "deletefile":
    hedef = yerel(args[1])
    if not os.path.isfile(hedef):
        sys.stderr.write("fake rclone: dosya yok\n")
        sys.exit(1)
    os.remove(hedef)
else:
    sys.stderr.write(f"fake rclone: bilinmeyen komut {komut}\n")
    sys.exit(2)
