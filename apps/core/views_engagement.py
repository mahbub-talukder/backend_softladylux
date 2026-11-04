"""Contact form, newsletter and their admin views. SRS NTF-01, NTF-04, §7 (`contact/`,
`newsletter/`, admin `contact-messages/`), `contact.manage` for the admin side (§2.2)."""

import csv

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.http import HttpResponse
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import require
from apps.core import audit

from .models import ContactMessage, Subscriber
from .notifications import enqueue
from .throttles import ContactThrottle


def _email(value) -> str:
    value = str(value or "").strip().lower()
    try:
        validate_email(value)
    except DjangoValidationError:
        raise serializers.ValidationError({"email": "Enter a valid email address."}) from None
    return value


class ContactView(APIView):
    """NTF-01: stored, emailed to the support email, spam-protected by a honeypot field and a rate
    limit. A filled honeypot gets the normal answer but nothing is stored or sent."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ContactThrottle]

    def post(self, request):
        d = request.data
        if str(d.get("website", "")).strip():  # honeypot: real visitors never see this field
            return Response({"ok": True}, status=status.HTTP_201_CREATED)
        name = str(d.get("name", "")).strip()
        message = str(d.get("message", "")).strip()
        errors = {}
        if not 2 <= len(name) <= 120:
            errors["name"] = "Enter your name."
        if not 5 <= len(message) <= 3000:
            errors["message"] = "Write your message (5–3000 characters)."
        if errors:
            raise serializers.ValidationError(errors)
        phone = str(d.get("phone", "")).strip()[:20]
        m = ContactMessage.objects.create(
            name=name, phone=phone, email=_email(d.get("email")),
            subject=str(d.get("subject", "")).strip()[:150], message=message)
        enqueue("apps.core.notifications.send_contact_notice", m.pk)
        return Response({"ok": True}, status=status.HTTP_201_CREATED)


class NewsletterView(APIView):
    """NTF-04: stores the email (idempotent) and sends the welcome email once."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ContactThrottle]

    def post(self, request):
        sub, created = Subscriber.objects.get_or_create(email=_email(request.data.get("email")))
        if created:
            enqueue("apps.core.notifications.send_newsletter_welcome", sub.pk)
        return Response({"ok": True}, status=status.HTTP_201_CREATED if created else
                        status.HTTP_200_OK)


class ContactMessageSerializer(serializers.ModelSerializer):
    handled_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ContactMessage
        fields = ["id", "created_at", "name", "phone", "email", "subject", "message", "is_handled",
                  "handled_by_name"]
        read_only_fields = ["name", "phone", "email", "subject", "message"]

    def get_handled_by_name(self, m):
        return str(m.handled_by) if m.handled_by else None


class ContactMessageViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                            mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """NTF-01: admin can view messages and mark them handled."""

    permission_classes = [require("contact.manage")]
    serializer_class = ContactMessageSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        qs = ContactMessage.objects.select_related("handled_by")
        h = self.request.query_params.get("handled")
        if h in ("1", "0"):
            qs = qs.filter(is_handled=(h == "1"))
        return qs

    def perform_update(self, ser):
        handled = ser.validated_data.get("is_handled", ser.instance.is_handled)
        obj = ser.save(handled_by=self.request.user if handled else None)
        audit.log(self.request, "handled" if handled else "unhandled", "ContactMessage", obj.pk)


class SubscriberListView(ListAPIView):
    permission_classes = [require("contact.manage")]
    queryset = Subscriber.objects.all()

    def get_serializer_class(self):
        class S(serializers.ModelSerializer):
            class Meta:
                model = Subscriber
                fields = ["id", "email", "subscribed_at"]

        return S


class SubscriberExportView(APIView):
    """NTF-04: newsletter export to CSV."""

    permission_classes = [require("contact.manage")]

    def get(self, request):
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="newsletter.csv"'
        w = csv.writer(resp)
        w.writerow(["email", "subscribed_at"])
        for s in Subscriber.objects.all():
            w.writerow([s.email, s.subscribed_at.isoformat()])
        return resp
