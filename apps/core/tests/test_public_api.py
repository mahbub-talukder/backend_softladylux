# Covers SRS SET-06, THM-06, WEB-01..08, WEB-10, scenarios 1 and 15.
import pytest

from apps.core.models import Announcement, Banner, CarouselSlide, MediaAsset, Page, Video

from .helpers import image_file

pytestmark = pytest.mark.django_db
ADMIN = "/api/v1/admin"


def test_public_settings_hides_internal_fields(api_client):  # SET-06
    d = api_client.get("/api/v1/settings/").data
    assert d["company_name"] == "SoftLadyLux" and "delivery_fee_inside_dhaka" in d
    for hidden in ("mimsms_api_key", "mimsms_sender_id", "stale_draft_hours",
                   "low_stock_threshold", "order_number_prefix"):
        assert hidden not in d


def test_public_theme_default_signature_small(api_client):  # THM-13, THM-06 (<2 KB)
    r = api_client.get("/api/v1/theme/")
    assert r.data["primary"] == "#231E1C" and r.data["tokens"]["--color-primary"] == "#231E1C"
    assert len(r.content) < 2048


def test_home_empty_then_filled(api_client):  # WEB-06
    d = api_client.get("/api/v1/home/").data
    assert d["carousel"] == [] and len(d["trust"]) == 3
    assert len(d["categories"]) == 8  # seeded (CAT-01)
    m = MediaAsset.objects.create(file="x.png", kind="carousel", alt_text="a")
    CarouselSlide.objects.create(media=m, heading="Hi")
    CarouselSlide.objects.create(media=m, heading="Off", is_active=False)
    Video.objects.create(title="V", url="https://youtu.be/x")
    Announcement.objects.create(text="Free delivery")
    Banner.objects.filter(placement="home_promo").update(media=m, is_active=True)
    Banner.objects.filter(placement="home_offer").update(media=m, is_active=False)
    from apps.core import cache
    cache.invalidate()
    d = api_client.get("/api/v1/home/").data
    assert [s["heading"] for s in d["carousel"]] == ["Hi"]
    assert len(d["videos"]) == 1 and len(d["announcements"]) == 1
    assert [b["placement"] for b in d["banners"]] == ["home_promo"]


def test_home_shows_at_most_8_slides(api_client):  # WEB-01
    m = MediaAsset.objects.create(file="x.png", kind="carousel", alt_text="a")
    for i in range(10):
        CarouselSlide.objects.create(media=m, sort=i)
    assert len(api_client.get("/api/v1/home/").data["carousel"]) == 8


def test_static_page(api_client):  # WEB-07
    Page.objects.filter(slug="about").update(body="<p>Hello</p>")
    assert api_client.get("/api/v1/pages/about/").data["body"] == "<p>Hello</p>"
    assert api_client.get("/api/v1/pages/nope/").status_code == 404
    Page.objects.filter(slug="terms").update(is_active=False)
    assert api_client.get("/api/v1/pages/terms/").status_code == 404


def test_scenario_1_admin_edit_reflected_without_code_change(as_user, owner, api_client):
    """Scenario 1: logo, slide and address edits show on the public API at once (cache cleared)."""
    api_client.get("/api/v1/settings/")  # warm cache
    c = as_user(owner)
    logo = c.post(f"{ADMIN}/media/", {"file": image_file(), "kind": "logo", "alt_text": "Logo"},
                  format="multipart").data
    c.patch(f"{ADMIN}/settings/", {"logo": logo["id"], "address_line1": "12 Road, Gulshan"},
            format="json")
    slide = c.post(f"{ADMIN}/website/carousel/", {"media": logo["id"], "heading": "New"},
                   format="json")
    assert slide.status_code == 201
    pub = api_client.get("/api/v1/settings/").data
    assert pub["address_line1"] == "12 Road, Gulshan" and pub["logo"]["id"] == logo["id"]
    assert api_client.get("/api/v1/home/").data["carousel"][0]["heading"] == "New"
