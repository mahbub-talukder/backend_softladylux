from apps.catalog.tests.factories import product
from apps.core.models import SiteSettings


def configure_site(**kw):
    s = SiteSettings.load()
    s.bkash_number, s.nagad_number = "01711111111", "01822222222"
    s.delivery_fee_inside_dhaka, s.delivery_fee_outside_dhaka = 60, 120
    for k, v in kw.items():
        setattr(s, k, v)
    s.save()
    return s


def payload(variant, qty=1, **kw):
    base = {
        "lines": [{"variant": variant.id, "quantity": qty}],
        "name": "Nusrat Jahan", "phone": "01712345678", "email": "n@example.com",
        "area": "inside_dhaka", "district": "Dhaka", "thana": "Dhanmondi",
        "address_line": "House 12, Road 5", "note": "", "payment_method": "cod",
    }
    base.update(kw)
    return base


def bkash(variant, qty=1, trx="8N7A6D5EE7", **kw):
    return payload(variant, qty, payment_method="bkash", sender_number="01712345678",
                   trx_id=trx, **kw)


def one_variant(name="Plunge Bra", price="1000", on_hand=5, **kw):
    p = product(name, price=price, sizes=("34B",), colours=("Rose",), on_hand=on_hand, **kw)
    return p.variants.get()
