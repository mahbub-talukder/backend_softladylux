# Covers M6 seed data: create_owner (ACC-10/12, PRM-06) and seed_demo (staging only, idempotent).
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import Role, User
from apps.catalog.models import Product, SubCategory
from apps.core.models import AuditLog, Banner, CarouselSlide, MediaAsset, SiteSettings
from apps.shop.models import Coupon

pytestmark = pytest.mark.django_db


def run(name, *args, **kw):
    out = StringIO()
    call_command(name, *args, stdout=out, **kw)
    return out.getvalue()


# ------------------------------------------------------------------ create_owner


def test_create_owner_from_env_password(monkeypatch):
    monkeypatch.setenv("OWNER_PASSWORD", "a-long-secret-pass-1")
    out = run("create_owner", "--email", "Boss@Example.com", "--name", "The Boss")
    u = User.objects.get(email="boss@example.com")
    assert u.role == Role.OWNER and u.is_active and u.check_password("a-long-secret-pass-1")
    assert u.full_name == "The Boss" and "Sign in at /admin/login" in out
    assert AuditLog.objects.filter(action="create_owner").exists()
    assert "a-long-secret-pass-1" not in str(AuditLog.objects.values_list("before", "after"))


def test_create_owner_prompts_when_no_env(monkeypatch):
    monkeypatch.delenv("OWNER_PASSWORD", raising=False)
    answers = iter(["prompted-pass-12", "prompted-pass-12"])
    monkeypatch.setattr("getpass.getpass", lambda *_: next(answers))
    run("create_owner", "--email", "p@example.com")
    assert User.objects.get(email="p@example.com").check_password("prompted-pass-12")


def test_create_owner_prompt_mismatch_and_weak_password(monkeypatch):
    monkeypatch.delenv("OWNER_PASSWORD", raising=False)
    answers = iter(["one-long-password-1", "other-long-password"])
    monkeypatch.setattr("getpass.getpass", lambda *_: next(answers))
    with pytest.raises(CommandError, match="do not match"):
        run("create_owner", "--email", "p@example.com")
    monkeypatch.setenv("OWNER_PASSWORD", "short")
    with pytest.raises(CommandError, match="Password rejected"):
        run("create_owner", "--email", "p@example.com")
    assert User.objects.count() == 0


def test_second_owner_needs_force_and_duplicate_email_refused(monkeypatch):
    monkeypatch.setenv("OWNER_PASSWORD", "a-long-secret-pass-1")
    run("create_owner", "--email", "a@example.com")
    with pytest.raises(CommandError, match="already exists"):
        run("create_owner", "--email", "b@example.com")
    run("create_owner", "--email", "b@example.com", "--force")
    with pytest.raises(CommandError, match="already exists"):
        run("create_owner", "--email", "b@example.com", "--force")
    with pytest.raises(CommandError, match="valid email"):
        run("create_owner", "--email", "nope", "--force")
    assert User.objects.filter(role=Role.OWNER).count() == 2


# ------------------------------------------------------------------ seed_demo


def test_seed_demo_refuses_without_debug(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="staging only"):
        run("seed_demo")
    assert Product.objects.count() == 0


def test_seed_demo_override_flag_for_staging(settings):
    settings.DEBUG = False
    out = run("seed_demo", "--i-know-this-is-not-production")
    assert "Demo content ready" in out and Product.objects.count() == 8


@pytest.fixture
def demo(settings):
    settings.DEBUG = True
    run("seed_demo")


def test_seed_demo_creates_catalogue_photos_content(demo):
    assert Product.objects.filter(is_active=True).count() == 8
    bras = SubCategory.objects.filter(category__slug="bras").values_list("name", flat=True)
    assert set(bras) == {"Maternity Bra", "Air Bra", "Tube Bra"}
    p = Product.objects.get(name="Silk-Touch Plunge Bra")
    assert p.subcategory.name == "Air Bra" and p.variants.count() == 4  # 2 sizes x 2 colours
    assert p.images.count() == 2 and p.images.first().media.width >= 600  # §10 photo size
    assert all(v.on_hand == 12 for v in p.variants.all())
    assert p.variants.first().movements.filter(reason="opening_stock").exists()  # INV-08
    assert CarouselSlide.objects.count() == 2
    assert Banner.objects.filter(is_active=True).count() == 2
    assert Coupon.objects.filter(code="WELCOME10").exists()
    assert SiteSettings.load().address_line1 == "[STORE ADDRESS]"  # marked placeholder


def test_seed_demo_is_idempotent_and_keeps_owner_values(demo):
    s = SiteSettings.load()
    s.address_line1 = "12 Real Road"
    s.save()
    before = (Product.objects.count(), MediaAsset.objects.count(), Coupon.objects.count())
    run("seed_demo")
    assert (Product.objects.count(), MediaAsset.objects.count(), Coupon.objects.count()) == before
    assert SiteSettings.load().address_line1 == "12 Real Road"  # never overwritten


def test_seed_demo_reset_hides_demo_products_and_keeps_real_ones(demo):
    real = Product.objects.create(name="My Real Bra", slug="my-real-bra", price=1,
                                  category=Product.objects.first().category, collection="Mine",
                                  is_active=True)
    run("seed_demo", "--reset")
    # demo products have immutable stock movements (INV-08): hidden, not deleted
    assert Product.objects.filter(collection="Demo", is_active=True).count() == 0
    assert Product.objects.filter(collection="Demo").count() == 8
    real.refresh_from_db()
    assert real.is_active is True  # the owner's own product is untouched
    assert CarouselSlide.objects.count() == 0
    assert Banner.objects.filter(is_active=True).count() == 0 and Banner.objects.count() == 2
    assert not Coupon.objects.filter(code="WELCOME10").exists()


def test_seeding_again_after_reset_brings_the_demo_back_without_duplicates(demo):
    run("seed_demo", "--reset")
    run("seed_demo")
    assert Product.objects.filter(collection="Demo", is_active=True).count() == 8
    assert Product.objects.filter(collection="Demo").count() == 8


def test_reset_keeps_a_subcategory_that_a_real_product_uses(demo):
    sub = SubCategory.objects.get(name="Air Bra")
    Product.objects.create(name="Owner Product", slug="owner-product", price=1,
                           category=sub.category, subcategory=sub, collection="Mine")
    run("seed_demo", "--reset")
    assert SubCategory.objects.filter(name="Air Bra").exists()  # still in use: kept


def test_generated_drawings_differ_per_garment_and_meet_the_photo_size_rule(demo):
    from PIL import Image

    bra = Product.objects.get(name="Silk-Touch Plunge Bra").images.first().media
    top = Product.objects.get(name="Linen Wrap Top").images.first().media
    with Image.open(bra.file.path) as a, Image.open(top.file.path) as b:
        assert max(a.size) >= 600 and a.size == b.size == (900, 1200)  # §10 product photo size
        assert a.tobytes() != b.tobytes()  # a bra is not drawn like a top
    assert "[demo]" in bra.alt_text


def test_own_photos_folder_is_used_instead_of_drawings(settings, tmp_path):
    from PIL import Image

    settings.DEBUG = True
    Image.new("RGB", (1000, 1500), (10, 200, 30)).save(tmp_path / "silk-touch-plunge-bra-1.jpg")
    run("seed_demo", "--photos-dir", str(tmp_path))
    mine = Product.objects.get(name="Silk-Touch Plunge Bra").images.first().media
    assert (mine.width, mine.height) == (1000, 1500)
    other = Product.objects.get(name="Linen Wrap Top").images.first().media
    assert (other.width, other.height) == (900, 1200)  # no photo for it: generated drawing
