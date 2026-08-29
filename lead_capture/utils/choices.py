from django.db import models


class LeadStatusType(models.TextChoices):
    NEW = "new", "New"
    QUALIFIED = "qualified", "Qualified"
    CONTACTED = "contacted", "Contacted"
    CONVERTED = "converted", "Converted"
    DISQUALIFIED = "disqualified", "Disqualified"


class LeadCaptureFieldMode(models.TextChoices):
    HIDDEN = "hidden", "Hidden"
    OPTIONAL = "optional", "Optional"
    REQUIRED = "required", "Required"
