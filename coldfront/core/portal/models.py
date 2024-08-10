from django.db import models
from martor.models import MartorField
from django.utils import timezone

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
    
    def save(self):
        if self.hash is None:
            # Generate a random hash (5 characters), until it is unique
            import random
            import string
            hash = ''.join(random.choices(string.ascii_letters + string.digits, k=5))
            while News.objects.filter(hash=hash).exists():
                hash = ''.join(random.choices(string.ascii_letters + string.digits, k=5))
            self.hash = hash
        super().save()
    
    class Meta:
        verbose_name_plural = 'News'


class DocumentationArticle(models.Model):
    title = models.CharField(max_length=100)
    body = MartorField()
    publication_date = models.DateTimeField(default=timezone.now)
    last_updated = models.DateTimeField(default=timezone.now)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True)
    order = models.IntegerField(default=0)
    active = models.BooleanField(default=True)

    @property
    def children(self):
        return DocumentationArticle.objects.filter(parent=self)

    def save(self):
        self.last_updated = timezone.now()
        super().save()
            
    def __str__(self):
        return self.title

    class Meta:
        verbose_name_plural = 'Documentation Articles'