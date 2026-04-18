"""
marketplace/forms.py
====================
ModelForms and helper forms for the marketplace app.
Views import from here — keep this file in sync with models.py.
"""

from django import forms
from django.core.exceptions import ValidationError

from .models import (
    WorkerProfile,
    PortfolioItem,
    EmployerProfile,
    Job,
    JobApplication,
    Review,
    Skill,
    TradeCategory,
    NIGERIAN_STATES,
)


# ──────────────────────────────────────────────────────────────────────────────
#  WORKER PROFILE
# ──────────────────────────────────────────────────────────────────────────────

class WorkerProfileForm(forms.ModelForm):
    class Meta:
        model  = WorkerProfile
        fields = [
            'trade_category', 'experience_level', 'years_experience',
            'bio', 'state', 'lga', 'is_willing_to_relocate',
            'hourly_rate', 'daily_rate', 'availability',
        ]
        widgets = {
            'bio': forms.Textarea(attrs={'rows': 5,
                'placeholder': 'Describe your skills, experience, and the kind of work you do…'}),
            'lga': forms.TextInput(attrs={'placeholder': 'e.g. Ikeja'}),
        }


class PortfolioItemForm(forms.ModelForm):
    class Meta:
        model  = PortfolioItem
        fields = ['image', 'caption', 'trade_context', 'display_order']
        widgets = {
            'caption': forms.TextInput(attrs={'placeholder': 'Short description of this work'}),
        }


# ──────────────────────────────────────────────────────────────────────────────
#  EMPLOYER PROFILE
# ──────────────────────────────────────────────────────────────────────────────

class EmployerProfileForm(forms.ModelForm):
    class Meta:
        model  = EmployerProfile
        fields = [
            'company_name', 'company_type', 'industry',
            'about', 'logo', 'website', 'state', 'lga',
        ]
        widgets = {
            'about': forms.Textarea(attrs={'rows': 4,
                'placeholder': 'Tell workers about your company or project…'}),
            'website': forms.URLInput(attrs={'placeholder': 'https://'}),
        }


# ──────────────────────────────────────────────────────────────────────────────
#  JOB
# ──────────────────────────────────────────────────────────────────────────────

class JobForm(forms.ModelForm):
    class Meta:
        model  = Job
        fields = [
            'trade_category', 'required_skills', 'title', 'description',
            'job_type', 'pay_type', 'pay_min', 'pay_max', 'slots',
            'state', 'lga', 'is_remote', 'deadline',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 6,
                'placeholder': 'Describe the job, requirements, and what you expect…'}),
            'required_skills': forms.CheckboxSelectMultiple(),
            'deadline': forms.DateInput(attrs={'type': 'date'}),
            'lga': forms.TextInput(attrs={'placeholder': 'e.g. Victoria Island'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter skills to those belonging to the selected trade category
        if 'trade_category' in self.data:
            try:
                trade_id = self.data.get('trade_category')
                self.fields['required_skills'].queryset = (
                    Skill.objects.filter(category_id=trade_id, is_active=True)
                )
            except (ValueError, TypeError):
                self.fields['required_skills'].queryset = Skill.objects.none()
        elif self.instance.pk and self.instance.trade_category:
            self.fields['required_skills'].queryset = (
                Skill.objects.filter(
                    category=self.instance.trade_category, is_active=True
                )
            )
        else:
            self.fields['required_skills'].queryset = Skill.objects.none()

    def clean(self):
        cleaned = super().clean()
        pay_min = cleaned.get('pay_min')
        pay_max = cleaned.get('pay_max')
        if pay_min and pay_max and pay_min > pay_max:
            raise ValidationError('Minimum pay cannot be greater than maximum pay.')
        return cleaned


# ──────────────────────────────────────────────────────────────────────────────
#  JOB APPLICATION
# ──────────────────────────────────────────────────────────────────────────────

class JobApplicationForm(forms.ModelForm):
    class Meta:
        model  = JobApplication
        fields = ['cover_note']
        widgets = {
            'cover_note': forms.Textarea(attrs={'rows': 4,
                'placeholder': (
                    'Briefly introduce yourself and explain why you are '
                    'a good fit for this job…'
                )}),
        }


# ──────────────────────────────────────────────────────────────────────────────
#  REVIEW
# ──────────────────────────────────────────────────────────────────────────────

class ReviewForm(forms.ModelForm):
    class Meta:
        model  = Review
        fields = ['rating', 'comment']
        widgets = {
            'rating': forms.RadioSelect(
                choices=[(i, f'{i} star{"s" if i > 1 else ""}') for i in range(1, 6)]
            ),
            'comment': forms.Textarea(attrs={'rows': 3,
                'placeholder': 'Share your experience…'}),
        }


# ──────────────────────────────────────────────────────────────────────────────
#  JOB SEARCH / FILTER (unbound, used in JobListView)
# ──────────────────────────────────────────────────────────────────────────────

class JobFilterForm(forms.Form):
    q        = forms.CharField(
                required=False, label='Keyword',
                widget=forms.TextInput(attrs={'placeholder': 'e.g. Electrician…'}))
    trade    = forms.ModelChoiceField(
                queryset=TradeCategory.objects.filter(is_active=True),
                required=False, label='Trade', empty_label='All Trades')
    state    = forms.ChoiceField(
                choices=[('', 'All States')] + list(NIGERIAN_STATES),
                required=False, label='State')
    job_type = forms.ChoiceField(
                choices=[('', 'All Types')] + list(Job.JobType.choices),
                required=False, label='Job Type')
    pay_type = forms.ChoiceField(
                choices=[('', 'Any Pay Type')] + list(Job.PayType.choices),
                required=False, label='Pay Type')
    is_remote = forms.BooleanField(required=False, label='Remote only')