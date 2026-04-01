# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later
from django.db import models
from martor.models import MartorField
from django.utils import timezone
from django.db.models import Q


class Carousel(models.Model):
    title = models.CharField(max_length=100)
    description = models.TextField()
    image = models.ImageField(upload_to='carousel/')
    news = models.ForeignKey('News', on_delete=models.CASCADE, null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.title


class News(models.Model):
    title = models.CharField(max_length=100)
    body = MartorField()
    expiry_date = models.DateField(null=True, blank=True)
    publication_date = models.DateTimeField(default=timezone.now)
    hash = models.CharField(max_length=100, null=True, blank=True, unique=True, editable=False)

    def __str__(self):
        return self.title
    
    def save(self, *args, **kwargs):
        if self.hash is None:
            # Generate a random hash (5 characters), until it is unique
            import random
            import string
            hash = ''.join(random.choices(string.ascii_letters + string.digits, k=5))
            while News.objects.filter(hash=hash).exists():
                hash = ''.join(random.choices(string.ascii_letters + string.digits, k=5))
            self.hash = hash
        super().save(*args, **kwargs)

    class Meta:
        verbose_name_plural = 'News'


class DocumentationArticle(models.Model):
    title = models.CharField(max_length=100)
    body = MartorField(blank=True, null=True)
    publication_date = models.DateTimeField(default=timezone.now)
    last_updated = models.DateTimeField(default=timezone.now)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True)
    order = models.IntegerField(default=0)
    active = models.BooleanField(default=True)
    hash = models.CharField(max_length=100, null=True, blank=True, unique=True, editable=False)

    @property
    def children(self):
        return DocumentationArticle.objects.filter(parent=self, active=True).order_by('order')

    @property
    def is_empty(self):
        return len(self.body) == 0

    def save(self, *args, **kwargs):
        if self.hash is None:
            # Generate a random hash (5 characters), until it is unique
            import random
            import string
            hash = ''.join(random.choices(string.ascii_letters + string.digits, k=5))
            while News.objects.filter(hash=hash).exists():
                hash = ''.join(random.choices(string.ascii_letters + string.digits, k=5))
            self.hash = hash

        self.last_updated = timezone.now()
        super().save(*args, **kwargs)
            
    def __str__(self):
        return self.title

    class Meta:
        verbose_name_plural = 'Documentation Articles'


class AccountOnboardingRequest(models.Model):
    # New role values (Italian)
    ROLE_PHD = 'Studente di Dottorato'
    ROLE_RESEARCH_GRANT = 'Assegno di Ricerca'
    ROLE_RESEARCH_CONTRACT = 'Contratto di Ricerca'
    ROLE_RESEARCH_ASSIGNMENT = 'Incarico di Ricerca'
    ROLE_POSTDOC_ASSIGNMENT = 'Incarico Post-Doc'
    ROLE_COLLAB_ASSIGNMENT = 'Incarico di Collaborazione'
    ROLE_RTD_A = 'Ricercatore RTD-A'
    ROLE_RTD_B = 'Ricercatore RTD-B'
    ROLE_RTT = 'Ricercatore RTT'
    ROLE_ASSOCIATE_PROF = 'Professore Associato'
    ROLE_FULL_PROF = 'Professore Ordinario'
    ROLE_THESIS = 'Studente in tesi'
    ROLE_COURSE = 'Studente da un corso'
    ROLE_GUEST = 'Ospiti a vario titolo'

    ROLE_CHOICES = [
        (ROLE_PHD, 'Studente di Dottorato'),
        (ROLE_RESEARCH_GRANT, 'Assegno di Ricerca'),
        (ROLE_RESEARCH_CONTRACT, 'Contratto di Ricerca'),
        (ROLE_RESEARCH_ASSIGNMENT, 'Incarico di Ricerca'),
        (ROLE_POSTDOC_ASSIGNMENT, 'Incarico Post-Doc'),
        (ROLE_COLLAB_ASSIGNMENT, 'Incarico di Collaborazione'),
        (ROLE_RTD_A, 'Ricercatore RTD-A'),
        (ROLE_RTD_B, 'Ricercatore RTD-B'),
        (ROLE_RTT, 'Ricercatore RTT'),
        (ROLE_ASSOCIATE_PROF, 'Professore Associato'),
        (ROLE_FULL_PROF, 'Professore Ordinario'),
        (ROLE_THESIS, 'Studente in tesi'),
        (ROLE_COURSE, 'Studente da un corso'),
        (ROLE_GUEST, 'Ospiti a vario titolo'),
    ]

    STATUS_PENDING = 'Pending'
    STATUS_APPROVED = 'Approved'
    STATUS_REJECTED = 'Rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    username = models.CharField(max_length=150)
    given_name = models.CharField(max_length=150)
    surname = models.CharField(max_length=150)
    email = models.EmailField()
    # For UNIMORE SSO users this is populated; for external users it is blank/NULL
    unimore_id = models.CharField(max_length=150, blank=True, null=True)
    codice_fiscale = models.CharField(max_length=16, blank=True, null=True)
    role = models.CharField(max_length=40, choices=ROLE_CHOICES)
    expiration_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_PENDING)
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    course_project = models.ForeignKey(
        'project.Project',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='onboarding_requests',
        help_text='Corso (progetto) selezionato per gli utenti con ruolo "Studente da un corso".'
    )
    processed_in_ldap = models.BooleanField(default=False, help_text='Set to true once the LDAP provisioning job has created/updated the directory entry.')
    rejection_reason = models.TextField(blank=True, null=True, help_text='Optional reason shown to admins when the request is rejected.')
    identity_document = models.FileField(upload_to='identity_documents/', blank=True, null=True,
                                         help_text='Required for users without a UNIMORE account (government-issued ID, student card, etc.).')

    class Meta:
        ordering = ['-submitted_at']
        constraints = [
            models.UniqueConstraint(fields=['unimore_id', 'status'], condition=Q(status='Pending') & ~Q(unimore_id__isnull=True) & ~Q(unimore_id=''),
                                    name='unique_pending_onboarding_request_per_unimore_user'),
            models.UniqueConstraint(fields=['email', 'status'], condition=Q(status='Pending') & (Q(unimore_id__isnull=True) | Q(unimore_id='')),
                                    name='unique_pending_onboarding_request_per_external_email'),
        ]

    def __str__(self):
        return f"{self.username} - {self.role} ({self.status})"

    @property
    def is_unimore(self) -> bool:
        return bool(self.unimore_id)