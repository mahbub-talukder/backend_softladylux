"""Stock changes. SRS INV-08/INV-09 (every change logged, row-locked), rule 9 (never below zero)."""

from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import StockMovement


@transaction.atomic
def record_movement(variant, delta: int, reason: str, user=None, order_ref="",
                    lot_reference="", remarks="") -> StockMovement:
    """Apply `delta` to variant.on_hand under a row lock and log it in the same transaction."""
    from apps.catalog.models import Variant

    locked = Variant.objects.select_for_update().get(pk=variant.pk)
    after = locked.on_hand + delta
    if after < 0:
        raise ValidationError({"on_hand": "Stock cannot go below zero."})
    locked.on_hand = after
    locked.save(update_fields=["on_hand", "updated_at"])
    variant.on_hand = after
    return StockMovement.objects.create(
        variant=locked, delta_on_hand=delta, on_hand_after=after, reason=reason, user=user,
        order_ref=order_ref, lot_reference=lot_reference, remarks=remarks)
