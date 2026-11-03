# Covers SRS WEB-01..10, WEB-09 upload rules, ADM-10 audit log (Owner only).
import pytest

from apps.core.models import AuditLog, Banner, CarouselSlide, MediaAsset, Page

from .helpers import image_file

pytestmark = pytest.mark.django_db
A = "/api/v1/admin"


@pytest.fixture
def media(as_user, owner):
    r = as_user(owner).post(f"{A}/media/", {"file": image_file(), "kind": "carousel",
                                            "alt_text": "Alt"}, format="multipart")
    assert r.status_code == 201
    return r.data


def test_upload_stores_original_and_dimensions(media):  # WEB-09
    assert media["width"] == 20 and media["height"] == 10 and media["url"].startswith("/media/")
    assert MediaAsset.objects.count() == 1


@pytest.mark.parametrize("fmt,name", [("JPEG", "a.jpg"), ("WEBP", "a.webp")])
def test_upload_accepts_jpg_webp(as_user, owner, fmt, name):
    r = as_user(owner).post(f"{A}/media/", {"file": image_file(name, fmt), "kind": "product",
                                            "alt_text": "x"}, format="multipart")
    assert r.status_code == 201


def test_upload_rejections(as_user, owner, settings):  # WEB-09
    from django.core.files.uploadedfile import SimpleUploadedFile
    c = as_user(owner)
    base = {"kind": "banner", "alt_text": "x"}
    assert c.post(f"{A}/media/", {**base, "file": image_file("a.gif", "GIF")},
                  format="multipart").status_code == 400
    assert c.post(f"{A}/media/", {**base, "file": SimpleUploadedFile("a.png", b"notimage")},
                  format="multipart").status_code == 400
    assert c.post(f"{A}/media/", {"kind": "banner", "file": image_file()},
                  format="multipart").status_code == 400  # alt required
    big = SimpleUploadedFile("big.png", image_file().read() + b"0" * (5 * 1024 * 1024),
                             content_type="image/png")
    assert c.post(f"{A}/media/", {**base, "file": big}, format="multipart").status_code == 400


def test_slide_crud_and_audit(as_user, owner, media):  # WEB-01
    c = as_user(owner)
    r = c.post(f"{A}/website/carousel/", {"media": media["id"], "heading": "A", "sort": 1},
               format="json")
    sid = r.data["id"]
    assert r.data["media"]["url"] == media["url"]
    assert c.get(f"{A}/website/carousel/").data[0]["heading"] == "A"
    assert c.patch(f"{A}/website/carousel/{sid}/", {"heading": "B"},
                   format="json").data["heading"] == "B"
    assert c.get(f"{A}/website/carousel/{sid}/").status_code == 200
    assert c.put(f"{A}/website/carousel/{sid}/", {"media": media["id"], "heading": "P"},
                 format="json").data["heading"] == "P"
    assert c.delete(f"{A}/website/carousel/{sid}/").status_code == 204
    actions = set(AuditLog.objects.filter(model="CarouselSlide").values_list("action", flat=True))
    assert actions == {"create", "delete", "update"}


def test_max_eight_active_slides(as_user, owner, media):  # WEB-01
    c = as_user(owner)
    for _ in range(8):
        assert c.post(f"{A}/website/carousel/", {"media": media["id"]},
                      format="json").status_code == 201
    assert c.post(f"{A}/website/carousel/", {"media": media["id"]},
                  format="json").status_code == 400
    ok = c.post(f"{A}/website/carousel/", {"media": media["id"], "is_active": False},
                format="json")
    assert ok.status_code == 201
    assert c.patch(f"{A}/website/carousel/{ok.data['id']}/", {"is_active": True},
                   format="json").status_code == 400
    first = CarouselSlide.objects.filter(is_active=True).first()
    assert c.patch(f"{A}/website/carousel/{first.id}/", {"heading": "same"},
                   format="json").status_code == 200  # editing an active one is fine


def test_banners_fixed_two_edit_only(as_user, owner, media):  # WEB-02
    c = as_user(owner)
    rows = c.get(f"{A}/website/banners/").data
    assert sorted(b["placement"] for b in rows) == ["home_offer", "home_promo"]
    b = Banner.objects.get(placement="home_promo")
    r = c.patch(f"{A}/website/banners/{b.id}/", {"media": media["id"], "is_active": True,
                                                 "placement": "hacked"}, format="json")
    assert r.status_code == 200 and r.data["placement"] == "home_promo"
    assert c.get(f"{A}/website/banners/{b.id}/").status_code == 200
    assert c.put(f"{A}/website/banners/{b.id}/", {"heading": "H"}, format="json").status_code == 200
    assert c.post(f"{A}/website/banners/", {}, format="json").status_code == 405
    assert c.delete(f"{A}/website/banners/{b.id}/").status_code == 405


@pytest.mark.parametrize("path,body", [
    ("videos", {"title": "T", "url": "https://youtu.be/abc"}),
    ("announcements", {"text": "Free delivery over 2000"}),
    ("trust", {"title": "Gift wrap", "icon": "gift"}),
])
def test_simple_content_crud(as_user, owner, path, body):  # WEB-04/05/08
    c = as_user(owner)
    r = c.post(f"{A}/website/{path}/", body, format="json")
    assert r.status_code == 201
    assert len(c.get(f"{A}/website/{path}/").data) == (4 if path == "trust" else 1)
    assert c.get(f"{A}/website/{path}/{r.data['id']}/").status_code == 200
    assert c.put(f"{A}/website/{path}/{r.data['id']}/", {**body, "sort": 3},
                 format="json").data["sort"] == 3
    assert c.patch(f"{A}/website/{path}/{r.data['id']}/", {"is_active": False},
                   format="json").data["is_active"] is False
    assert c.delete(f"{A}/website/{path}/{r.data['id']}/").status_code == 204


def test_pages_edit_only(as_user, owner):  # WEB-07
    c = as_user(owner)
    p = Page.objects.get(slug="about")
    assert len(c.get(f"{A}/website/pages/").data) == 4
    assert c.patch(f"{A}/website/pages/{p.id}/", {"body": "<p>x</p>"},
                   format="json").data["body"] == "<p>x</p>"
    assert c.get(f"{A}/website/pages/{p.id}/").status_code == 200
    assert c.put(f"{A}/website/pages/{p.id}/", {"slug": "about", "title": "About us"},
                 format="json").data["title"] == "About us"
    assert c.post(f"{A}/website/pages/", {"slug": "new", "title": "n"},
                  format="json").status_code == 405


def test_website_needs_permission(as_user, make_staff):
    assert as_user(make_staff("orders.view")).get(f"{A}/website/videos/").status_code == 403
    assert as_user(make_staff("website.manage")).get(f"{A}/website/videos/").status_code == 200


def test_media_upload_permission(as_user, make_staff):
    s = make_staff("orders.view")
    assert as_user(s).post(f"{A}/media/", {}, format="multipart").status_code == 403


def test_audit_log_owner_only_and_filter(as_user, owner, make_staff):  # ADM-10
    c = as_user(owner)
    c.patch(f"{A}/settings/", {"tagline": "Soft"}, format="json")
    c.put(f"{A}/theme/", {"primary_hex": "#1F6B54"}, format="json")
    assert c.get(f"{A}/audit-log/").data["count"] == 2
    r = c.get(f"{A}/audit-log/?model=ThemeSettings")
    assert r.data["count"] == 1 and r.data["results"][0]["user"] == "Owner"
    assert as_user(make_staff("settings.manage", "theme.manage")).get(
        f"{A}/audit-log/").status_code == 403


def test_audit_masks_ip(as_user, owner):  # §9 privacy
    c = as_user(owner)
    c.patch(f"{A}/settings/", {"tagline": "x"}, format="json", REMOTE_ADDR="203.0.113.77")
    assert AuditLog.objects.first().ip_masked == "203.0.113.0"
