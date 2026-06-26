import os

files_to_patch = [
    r"bot\config.py",
    r"bot\texts.py",
    r"bot\__main__.py",
    r"bot\api\routes\admin.py",
    r"bot\api\routes\payments.py",
    r"bot\api\routes\webhooks.py",
]

for filepath in files_to_patch:
    if not os.path.exists(filepath):
        continue
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    
    content = content.replace("lava", "platega")
    content = content.replace("Lava", "Platega")
    content = content.replace("LAVA", "PLATEGA")
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

print("Replaced all remaining lava->platega.")
