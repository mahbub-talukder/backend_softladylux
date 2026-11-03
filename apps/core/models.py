from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base for all tables.

    SRS ARC-11: every table has created_at / updated_at. Soft delete
    (is_active / deleted_at) is added per model where SRS §5 rule 11 requires it.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
