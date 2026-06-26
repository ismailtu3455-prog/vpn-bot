import os

files_to_patch = [
    r"bot\handlers\payments.py",
    r"bot\handlers\admin.py",
    r"bot\keyboards\inline.py",
    r"bot\services\platega.py",
]

for filepath in files_to_patch:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 1. Rename internal code references
    content = content.replace("lava", "platega")
    content = content.replace("Lava", "Platega")
    content = content.replace("LAVA", "PLATEGA")
    
    # 2. Update UI texts specifically
    # In inline.py:
    # "СБП (вручную)" was recently renamed to "СБП". The user wants:
    # 1. The old Tome (СБП) -> "СБП (вручную)"
    # 2. The old Lava (Platega) -> "СБП"
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

print("Replaced all lava->platega.")
