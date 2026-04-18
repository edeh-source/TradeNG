from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import login, logout
from django.views import View

from .forms import RegisterForm, LoginForm


class RegisterView(View):
    """
    Handles the 3-step registration wizard at accounts:register.

    GET  → render the empty form (step 1 is shown by JS by default).
    POST → validate the whole form; on success redirect to login; on failure
           re-render so Django template error tags auto-jump JS to the right step.
    """

    template_name = 'accounts/register.html'

    def get(self, request):
        if request.user.is_authenticated:
            return redirect('/')

        form = RegisterForm()
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        if request.user.is_authenticated:
            return redirect('/')

        form = RegisterForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(
                request,
                'Your account has been created! Sign in to get started.'
            )
            return redirect('signin')

        return render(request, self.template_name, {'form': form})


class SignInView(View):
    """
    Handles email + password login at accounts:signin.

    GET  → render the empty login form.
    POST → validate credentials; on success log the user in and redirect to
           the 'next' param (or home); on failure re-render with errors.

    'Remember me' behaviour:
        Checked   → session persists for 2 weeks (SESSION_COOKIE_AGE default).
        Unchecked → session expires when the browser is closed.
    """

    template_name = 'accounts/login.html'

    def get(self, request):
        if request.user.is_authenticated:
            return redirect('marketplace:dashboard')

        form = LoginForm()
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        if request.user.is_authenticated:
            return redirect('marketplace:dashboard')

        form = LoginForm(request.POST)

        if form.is_valid():
            user = form.get_user()

            # "Remember me" — if unchecked, expire session on browser close
            if not request.POST.get('remember'):
                request.session.set_expiry(0)

            login(request, user)

            messages.success(request, f'Welcome back, {user.username}!')

            # Honour the ?next= redirect set by @login_required, etc.
            next_url = request.GET.get('next') or request.POST.get('next') or 'marketplace:dashboard'
            return redirect(next_url)

        # Invalid credentials — re-render with form errors
        return render(request, self.template_name, {'form': form})


class SignOutView(View):
    """POST-only logout endpoint."""

    def post(self, request):
        logout(request)
        messages.info(request, 'You have been signed out.')
        return redirect('signin')