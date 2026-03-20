"""
Sewoo POS Receipt Printer controller (ESC/POS via /dev/usb/lp0)
Vendor: 1fc9  Product: 2016
"""

from escpos.printer import File


DEVICE = "/dev/usb/lp0"


def get_printer():
    return File(DEVICE)


# ── Basic usage examples ───────────────────────────────────────────────────

def print_text(text: str):
    p = get_printer()
    p.text(text + "\n")
    p.cut()


def print_receipt(lines: list[str], title: str = ""):
    p = get_printer()

    if title:
        p.set(align="center", bold=True, double_height=True, double_width=True)
        p.text(title + "\n")
        p.set()                          # reset to defaults

    for line in lines:
        p.text(line + "\n")

    p.cut()


def print_barcode(data: str, bc_type: str = "CODE128"):
    """bc_type: CODE128 | EAN13 | EAN8 | CODE39 | ITF | CODABAR | UPC-A"""
    p = get_printer()
    p.barcode(data, bc_type)
    p.cut()


def print_qr(data: str, size: int = 6):
    p = get_printer()
    p.qr(data, size=size)
    p.cut()


# ── Demo ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = get_printer()

    # Title
    p.set(align="center", bold=True, double_height=True)
    p.text("SEWOO PRINTER TEST\n")
    p.set()

    # Divider
    p.text("-" * 32 + "\n")

    # Normal text
    p.set(align="left")
    p.text("Item 1             $10.00\n")
    p.text("Item 2              $5.00\n")
    p.text("-" * 32 + "\n")

    # Bold total
    p.set(bold=True)
    p.text("TOTAL              $15.00\n")
    p.set()

    # QR code
    p.set(align="center")
    p.text("\nScan to visit:\n")
    p.qr("https://example.com", size=4)

    p.cut()
    print("Printed successfully.")
