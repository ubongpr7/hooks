from account.forms import ContactUsForm
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, JsonResponse
from django.conf import settings
from django.contrib.auth import login, get_user_model, logout, authenticate
from django.contrib import messages
from django.core.mail import EmailMessage
from django.core.mail import send_mail
from django.shortcuts import render, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import strip_tags
from .models import Subscription, StripeCustomer, Plan
import stripe
import uuid
from datetime import datetime

def stage(request):
  return render(request, 'stage.html')

def login_view(request):
  if request.user.is_authenticated:
    return redirect('hooks:upload')

  if request.method == 'POST':
    email = request.POST['email']
    password = request.POST['password']

    user = authenticate(request, username=email, password=password)

    if user is not None:
      if user.verification_token is None:
        _login(request, user)

        try:
          return redirect(request.session.get('next'))
        except:
          return redirect('hooks:upload')
      else:
        messages.error(
          request,
          'Your Email Address Is Not Verified. Please Verify Your Email Before Logging In.'
        )
    else:
      messages.error(request, 'Invalid Username or Password. Please Try Again.')

  next = request.GET.get('next', '')
  request.session['next'] = next
  return render(
    request,
    'registration/login.html',
  )

def logout_user(request):
  if request.user.is_authenticated:
    logout(request)

  return redirect('account:home')

def home(request):
  if request.user.is_authenticated:
    return redirect('hooks:upload')

  contact_us_form = ContactUsForm(request.POST or None)

  if request.method == 'POST':
    if contact_us_form.is_valid():
      try:
        contact_us_form.send()
      except Exception as e:
        print(f'An error occurred while sending contact us message {e}')
        messages.error(request, 'Failed To Send Message')
        return redirect(reverse('account:home') + '#Contact')

      messages.success(request, 'Message Sent Successfully')
      return redirect(reverse('account:home') + '#Contact')

  return render(
    request,
    'home.html',
    {
      'contact_us_form': contact_us_form,
      'plans': Plan.objects.all(),
    },
  )

def terms_and_conditions(request):
  return render(request, 'terms_and_conditions.html')

def privacy_policy(request):
  return render(request, 'privacy_policy.html')

def refund_policy(request):
  return render(request, 'refund_policy.html')

def affiliate_program(request):
  return render(request, 'affiliate_program.html')

def register(request):
  if request.method == 'POST':
    stripe.api_key = settings.STRIPE_SEC_KEY

    checkout_session_id = request.POST.get('session_id')

    name = request.POST.get('name')
    email = request.POST.get('email')
    password1 = request.POST.get('password1')
    password2 = request.POST.get('password2')

    if len(password1) < 6:
      messages.error(request, 'At Least 6 Characters Are Required')
      return render(
        request,
        'registration/register.html',
        context={'session_id': checkout_session_id}
      )

    if password1 != password2:
      messages.error(request, 'Passwords Do Not Match.')
      return render(
        request,
        'registration/register.html',
        context={'session_id': checkout_session_id}
      )

    User = get_user_model()
    if User.objects.filter(email=email).exists():
      messages.error(request, 'This Email Is Already Registered.')
      return render(
        request,
        'registration/register.html',
        context={'session_id': checkout_session_id}
      )

    user = User.objects.create_user(email=email, password=password1)
    user.first_name = name
    user.save()

    if checkout_session_id is None:
      free_plan = Plan.objects.get(id=3)
      customer = stripe.Customer.create(
        email=user.email,
        name=user.first_name,
      )
      stripe_customer = StripeCustomer(
        user=user, stripe_customer_id=customer.id
      )
      stripe_customer.save()
      subscription = Subscription(
        plan=free_plan,
        hooks=free_plan.hook_limit,
        merge_credits=free_plan.hook_limit * 5,
        customer=stripe_customer,
        stripe_subscription_id=None
      )
      subscription.save()
      user.subscription = subscription

      verification_token = str(uuid.uuid4())
      user.verification_token = verification_token

      user.save()

      send_html_email2(
        subject='Welcome to HooksMaster.io – Verify Your Email To Continue',
        message=None,
        from_email=settings.EMAIL_HOST_USER,
        to_email=user.email,
        html_file='verification.html',
        context={
          'first_name':
            user.first_name,
          'verification_url':
            settings.DOMAIN
            + reverse('account:verify', kwargs={'token': verification_token}),
        },
      )

      return render(
        request,
        'registration/register.html',
        context={
          'price_id': 'free',
          'success': True
        }
      )
    else:
      checkout_session = stripe.checkout.Session.retrieve(checkout_session_id)
      stripe_customer_id = checkout_session.customer

      customer_id = 0
      try:
        customer = StripeCustomer.objects.get(
          stripe_customer_id=stripe_customer_id
        )

        if customer is not None:
          customer.user = user
          customer.save()

          customer_id = customer.id
      except StripeCustomer.DoesNotExist:
        new_customer = StripeCustomer(
          user=user, stripe_customer_id=stripe_customer_id
        )
        new_customer.save()

        customer_id = new_customer.id

      try:
        subscription = Subscription.objects.get(customer_id=customer_id)

        if subscription is not None:
          user.subscription = subscription
          user.save()
      except Exception as _:
        messages.error(request, "Subscription Failed. Please Try Again Later.")
        return render(
          request,
          'registration/register.html',
          context={'session_id': checkout_session_id}
        )

      send_confirmation_email(email, user.first_name)

      _login(request, user)

      return redirect("hooks:upload")
  elif request.method == 'GET':
    checkout_session_id = request.GET.get('session_id')

    return render(
      request,
      'registration/register.html',
      context={'session_id': checkout_session_id}
    )

@csrf_exempt
def stripe_webhook(request):
  stripe.api_key = settings.STRIPE_SEC_KEY

  endpoint_secret = settings.STRIPE_ENDPOINT_SECRET
  payload = request.body
  sig_header = request.META['HTTP_STRIPE_SIGNATURE']

  event = None
  try:
    event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
  except ValueError as _:
    return HttpResponse(status=400)
  except stripe.error.SignatureVerificationError as _:
    return HttpResponse(status=400)

  event_type = event['type']
  event_object = event['data']['object']

  if event_type == 'invoice.payment_succeeded':
    if event_object.billing_reason == 'subscription_create':
      try:
        customer_id = event_object.customer

        customer = None
        try:
          customer = StripeCustomer.objects.get(stripe_customer_id=customer_id)
        except StripeCustomer.DoesNotExist:
          customer = StripeCustomer(user=None, stripe_customer_id=customer_id)
          customer.save()

        prev_sub = None
        prev_sub_hooks = 0
        prev_sub_merges = 0
        try:
          prev_sub = Subscription.objects.get(customer_id=customer.id)

          if prev_sub is not None:
            if prev_sub.stripe_subscription_id is not None:
              stripe.Subscription.delete(prev_sub.stripe_subscription_id)

            prev_sub_hooks = prev_sub.hooks
            prev_sub_merges = prev_sub.merge_credits
        except Subscription.DoesNotExist:
          pass

        subscription_id = event_object.subscription
        price_id = event_object.lines.data[0].price.id
        plan = Plan.objects.get(stripe_price_id=price_id)
        subscription = Subscription(
          plan=plan,
          stripe_subscription_id=subscription_id,
          customer=customer,
          hooks=plan.hook_limit + prev_sub_hooks,
          merge_credits=(plan.hook_limit * 5) + prev_sub_merges
        )
        subscription.save()

        if customer.user is not None:
          customer.user.subscription = subscription
          customer.user.save()

          if prev_sub is not None:
            prev_sub.delete()
      except Exception as e:
        print(
          datetime.now().strftime("%H:%M:%S")
          + f': Error in stripe webhook: {e}'
        )
    elif event_object.billing_reason == 'subscription_cycle':
      try:
        price_id = event_object.lines.data[0].price.id
        plan = Plan.objects.get(stripe_price_id=price_id)

        subscription_id = event_object.subscription
        subscription = Subscription.objects.get(
          stripe_subscription_id=subscription_id
        )
        subscription.hooks = plan.hook_limit + subscription.hooks
        subscription.merge_credits = (
          plan.hook_limit * 5
        ) + subscription.merge_credits
        subscription.save()
      except Exception as e:
        print(
          datetime.now().strftime("%H:%M:%S")
          + f': Error in stripe webhook: {e}'
        )
  elif event_type == 'invoice.payment_failed':
    if event_object.billing_reason == 'subscription_create':
      messages.error(
        request,
        'Checkout error. Couldn\'t Complete Subsrciption Successfully. Please try again later.'
      )

      print(
        datetime.now().strftime("%H:%M:%S") +
        ': Payment Failed. Couldn\'t Complete Subsrciption Successfully. Please try again later.'
      )
    elif event_object.billing_reason == 'subscription_cycle':
      messages.error(
        request,
        'Checkout error. Couldn\'t Renew Subsrciption Successfully. Please try again later.'
      )

      print(
        datetime.now().strftime("%H:%M:%S") +
        ': Payment Failed. Couldn\'t Renew Subsrciption Successfully. Please try again later.'
      )
  elif event_type == 'customer.subscription.deleted':
    if event_object.cancel_at_period_end:
      customer_id = event_object.customer

      try:
        customer = StripeCustomer.objects.get(stripe_customer_id=customer_id)
      except StripeCustomer.DoesNotExist:
        return HttpResponse(status=404)

      sub = Subscription.objects.get(customer_id=customer.id)
      sub.hooks = 0
      sub.merge_credits = 0
      sub.save()

  return HttpResponse(status=200)

@login_required
def manage_subscription(request):
  credits_left = request.user.subscription.hooks
  total_credits = max(request.user.subscription.plan.hook_limit, credits_left)

  current_period_end = 0
  if request.user.subscription.stripe_subscription_id is not None:
    stripe.api_key = settings.STRIPE_SEC_KEY

    subscription = stripe.Subscription.retrieve(
      request.user.subscription.stripe_subscription_id
    )

    current_period_end = int(subscription['current_period_end'])
  else:
    current_period_end = request.user.subscription.current_period_end

  now = int(datetime.now().timestamp())
  days_left = int((current_period_end-now) / 60 / 60 / 24)
  days_left = max(-1, days_left)
  days_left += 1

  return render(
    request,
    'subscription.html',
    context={
      'total_credits':
        total_credits,
      'credits_left':
        credits_left,
      'cur_plan':
        request.user.subscription.plan,
      'price_per_merge':
        f"{(request.user.subscription.plan.price_per_hook / 5):.2f}",
      'plans':
        Plan.objects.all(),
      'days_left':
        days_left,
    }
  )

@login_required
def billing_portal(request):
  stripe.api_key = settings.STRIPE_SEC_KEY

  try:
    customer = StripeCustomer.objects.get(user_id=request.user.id)

    session = stripe.billing_portal.Session.create(
      customer=customer.stripe_customer_id,
      return_url=settings.DOMAIN + reverse('account:home'),
    )

    return redirect(session.url)
  except Exception as _:
    return redirect(reverse('account:home'))

def verify(request, token):
  try:
    user = get_user_model().objects.get(verification_token=token)

    if user is not None:
      user.verification_token = None
      user.save()

      _login(request, user)
      return redirect('hooks:upload')
  except:
    return redirect(reverse('account:home'))

def subscribe(request, price_id):
  if request.method == 'GET':
    try:
      stripe.api_key = settings.STRIPE_SEC_KEY

      success_path = request.GET.get('success_path')
      cancel_path = request.GET.get('cancel_path')

      customer = None
      if request.user.is_authenticated:
        customer = request.user.subscription.customer.stripe_customer_id

      checkout_session = stripe.checkout.Session.create(
        customer=customer,
        success_url=settings.DOMAIN + success_path +
        ('&' if '?' in success_path else '?')
        + 'session_id={CHECKOUT_SESSION_ID}',
        cancel_url=settings.DOMAIN + cancel_path,
        payment_method_types=['card'],
        mode='subscription',
        line_items=[{
          'price': price_id,
          'quantity': 1,
        }]
      )

      return redirect(checkout_session.url)
    except Exception as _:
      return redirect(reverse('account:home'))

@login_required
def add_credits(request, kind):
  if request.method == 'POST':
    if int(request.POST.get('credits_number')
           ) >= 1 and request.user.subscription.plan.name.lower() != 'free':
      try:
        stripe.api_key = settings.STRIPE_SEC_KEY

        unit_amount = 0
        if kind == 'hook':
          unit_amount = float(request.user.subscription.plan.price_per_hook)
        elif kind == 'merge':
          unit_amount = float(request.user.subscription.plan.price_per_hook / 5)

        checkout_session = stripe.checkout.Session.create(
          customer=request.user.subscription.customer.stripe_customer_id,
          success_url=settings.DOMAIN + reverse('account:add_credits_success')
          + f'?amount={request.POST.get("credits_number")}&kind={kind}',
          cancel_url=settings.DOMAIN + reverse('account:add_credits_cancel'),
          payment_method_types=['card'],
          line_items=[
            {
              'price_data':
                {
                  'currency': 'usd',
                  'product_data':
                    {
                      'name':
                        f'{request.POST.get("credits_number")} {kind.title()} Credits',
                    },
                  'unit_amount': int(round(unit_amount * 100)),
                },
              'quantity': int(request.POST.get('credits_number')),
            },
          ],
          mode='payment',
        )

        return redirect(checkout_session.url)
      except Exception as _:
        return redirect(reverse('account:home'))

@login_required
def add_credits_success(request):
  if request.method == 'GET':
    new_credits = int(request.GET.get('amount'))
    kind = request.GET.get('kind')

    if kind == 'hook':
      request.user.subscription.hooks += new_credits
    elif kind == 'merge':
      request.user.subscription.merge_credits += new_credits

    request.user.subscription.save()

    return redirect(reverse('account:manage_subscription') + '?recheck=true')

def add_credits_cancel(request):
  return redirect(reverse('account:manage_subscription'))

@login_required
def upgrade_subscription(request, price_id):
  return subscribe(request, price_id)

@login_required
def downgrade_subscription(request):
  try:
    if request.user.subscription.plan.id == 2:
      subscription = stripe.Subscription.retrieve(
        request.user.subscription.stripe_subscription_id
      )

      stripe.Subscription.modify(
        subscription.id,
        items=[
          {
            'id': subscription['items']['data'][0].id,
            'price': settings.STRIPE_PRICE_ID_PRO,
          }
        ],
        proration_behavior='none',
      )

      pro_plan = Plan.objects.get(id=1)
      request.user.subscription.plan = pro_plan
      request.user.subscription.save()

      return redirect(reverse('account:manage_subscription') + '?recheck=true')
  except Exception as e:
    return redirect(reverse('account:manage_subscription'))

@login_required
def cancel_subscription(request):
  stripe.api_key = settings.STRIPE_SEC_KEY

  try:
    subscription = stripe.Subscription.retrieve(
      request.user.subscription.stripe_subscription_id
    )
    stripe.Subscription.modify(
      subscription.id,
      cancel_at_period_end=True,
    )

    free_plan = Plan.objects.get(id=3)
    request.user.subscription.plan = free_plan
    request.user.subscription.stripe_subscription_id = None
    request.user.subscription.current_period_end = subscription.current_period_end
    request.user.subscription.save()

    return redirect(reverse('account:manage_subscription') + '?recheck=true')
  except Exception as _:
    return redirect(reverse('account:manage_subscription'))

@login_required
def subscription(request):
  sub = request.user.subscription

  return JsonResponse(
    {
      'plan_name': sub.plan.name.lower(),
      'stripe_subscription_id': sub.stripe_subscription_id,
      'hooks': sub.hooks,
      'merge_credits': sub.merge_credits,
      'current_period_end': sub.current_period_end
    }
  )

def send_html_email2(
  subject, message, from_email, to_email, html_file, context
):
  html_content = render_to_string(html_file, context)

  text_content = strip_tags(html_content)

  send_mail(
    subject, text_content, from_email, [to_email], html_message=html_content
  )

def send_confirmation_email(email, name):
  # HTML email content
  logi_url = settings.DOMAIN + "login"
  if name is None:
    name = "there"
  html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Welcome to HooksMaster.io</title>
</head>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <div style="max-width: 600px; margin: 0 auto;">
    <h2 style="color: #2c3e50;">Hi {name},</h2>
<p>Welcome to <strong>HooksMaster.io</strong>! Your journey to creating high-converting video hooks effortlessly starts here. Your account has been successfully created, and you’re ready to optimize your ads.</p>

<h3 style="color: #2c3e50;">Next Steps:</h3>
<ul>
<li><strong>Log in:</strong> <a href="https://hooksmaster.io/login" style="color: #3498db;">Login to HooksMaster.io</a></li>
<li><strong>Get Started:</strong> Prepare your hooks and generate winning creatives.</li>
</ul>

<p>If you need support, we’re here to help. Feel free to reach out to us at <a href="mailto:support@hooksmaster.io" style="color: #3498db;">support@hooksmaster.io</a>.</p>

<p>Let’s create some high-converting hooks together!</p>

<a href="https://hooksmaster.io/login" style="display: inline-block; padding: 10px 20px; background-color: #3498db; color: white; text-decoration: none; border-radius: 5px; font-weight: bold;">Login Now</a>

<p>Best regards,</p>
<p><strong>The HooksMaster.io Team</strong></p>
</div>
</body>
</html>
    """

  email_message = EmailMessage(
    subject="Welcome to HooksMaster.io – Your Account is Ready!",
    body=html_content,
    from_email=settings.EMAIL_HOST_USER,
    to=[email],
  )
  email_message.content_subtype = "html"  # This is required to send the email as HTML
  email_message.send(fail_silently=True)

def _login(request, user):
  backend = "django.contrib.auth.backends.ModelBackend"
  user.backend = backend
  login(request, user, backend=backend)
