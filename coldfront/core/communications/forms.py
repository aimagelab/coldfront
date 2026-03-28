# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from django import forms
from martor.fields import MartorFormField


class BroadcastEmailForm(forms.Form):
    subject = forms.CharField(max_length=255, widget=forms.TextInput(attrs={'class': 'form-control'}))
    body = MartorFormField()
    create_news = forms.BooleanField(required=False, label='Also publish as a News item immediately')
