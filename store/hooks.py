import locale,paypalrestsdk,pagseguro,os
from django.utils.translation import ugettext as _
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse as response
from django.template import Template,Context
from django.http import HttpResponseRedirect as redirect
from shipping.codes import CorreiosCode
from shipping.fretefacil import FreteFacilShippingService
from shipping.correios import CorreiosShippingService
from shipping.models import DeliverableProperty
from mezzanine.conf import settings
from cartridge.shop.utils import set_shipping
from cartridge.shop.forms import OrderForm
from cartridge.shop.models import Cart
from cartridge.shop.checkout import CheckoutError

# Deprecated
def fretefacil_shipping_handler(request, form, order=None):
    if request.session.get("free_shipping"): return
    settings.use_editable()
    if form is not None: user_postcode = form.cleaned_data['shipping_detail_postcode']
    else: user_postcode = settings.STORE_POSTCODE 
    shippingservice = FreteFacilShippingService()
    cart = Cart.objects.from_request(request)
    delivery_value = 0.0
    if cart.has_items():
        for product in cart:
            properties = DeliverableProperty.objects.filter(sku=product.sku)
            if len(properties) > 0:
                props = properties[0]
                deliverable = shippingservice.create_deliverable(settings.STORE_POSTCODE,
                                                                 user_postcode,
                                                                 props.width,
                                                                 props.height,
                                                                 props.length,
                                                                 props.weight)
                delivery_value += float(shippingservice.delivery_value(deliverable))
    set_shipping(request, _("Correios"),delivery_value)

def correios_create_deliverable(obj,service,store_postcode,user_postcode,width,height,length,weight):
    obj.cep_origem = store_postcode
    obj.altura = height
    obj.largura = width
    obj.comprimento = length
    obj.peso = weight
    obj.servico = service
    return {
        'postcode':user_postcode,
        'service':service
    }


def correios_delivery_value(shippingservice,deliverable):
    shippingservice(deliverable['postcode'],deliverable['service'])
    return '.'.join(shippingservice.results[deliverable['service']][1].split(','))

def sedex_shipping_handler(request, form, order=None):
    if request.session.get("free_shipping"): return
    settings.use_editable()
    if form is not None: user_postcode = form.cleaned_data['shipping_detail_postcode']
    else: user_postcode = settings.STORE_POSTCODE 
    shippingservice = CorreiosShippingService()
    cart = Cart.objects.from_request(request)
    delivery_value = 0.0
    if cart.has_items():
        for product in cart:
            properties = DeliverableProperty.objects.filter(sku=product.sku)
            if len(properties) > 0:
                props = properties[0]
                deliverable = correios_create_deliverable(shippingservice,
                                                          'SEDEX',
                                                          settings.STORE_POSTCODE,
                                                          user_postcode,
                                                          props.width,
                                                          props.height,
                                                          props.length,
                                                          props.weight)
                delivery_value += float(correios_delivery_value(shippingservice,deliverable))
    set_shipping(request, _("Correios"),delivery_value)

def paypal_api():
	try:
		PAYPAL_CLIENT_ID = settings.PAYPAL_CLIENT_ID
		PAYPAL_CLIENT_SECRET = settings.PAYPAL_CLIENT_SECRET
	except AttributeError:
		raise ImproperlyConfigured(_("Credenciais de acesso ao paypal estao faltando, "
								 "isso inclui PAYPAL_CLIENT_ID e PAYPAL_SECRET "
								 "basta inclui-las no settings.py para serem utilizadas "
								 "no processador de pagamentos do paypal."))

	if settings.PAYPAL_SANDBOX_MODE: mode = 'sandbox'
	else: mode = 'live'

	api = paypalrestsdk.set_config(
		mode = mode,
		client_id = PAYPAL_CLIENT_ID,
		client_secret = PAYPAL_CLIENT_SECRET
	)

	os.environ['PAYPAL_MODE'] = mode
	os.environ['PAYPAL_CLIENT_ID'] = PAYPAL_CLIENT_ID
	os.environ['PAYPAL_CLIENT_SECRET'] = PAYPAL_CLIENT_SECRET

def pagseguro_api():
	api = pagseguro.PagSeguro(email=settings.PAGSEGURO_EMAIL_COBRANCA, 
				  			 token=settings.PAGSEGURO_TOKEN)
	return api

def paypal_payment(request,items,price,currency):
	paypal_api()
	server_host = request.get_host()
	payment = paypalrestsdk.Payment({
		"intent": "sale",
		"payer": {
			"payment_method": "paypal",
		},
		"redirect_urls" : {
			"return_url" : "http://%s/store/execute" % server_host,
			"cancel_url" : "http://%s/store/cancel" % server_host
		},
		"transactions": [{
			"item_list":{ "items":items	},
			"amount": {
				"total": '%.2f' % price,
				"currency": currency
			},
			"description": "Compra de Produtos na loja."
		}]
	})
	if payment.create(): return payment.id
	else: raise CheckoutError(payment.error)

def multiple_payment_handler(request, order_form, order):
	data = order_form.cleaned_data
	shipping = order.shipping_total
	code = CorreiosCode()
	shipping_data = code.consulta(order.billing_detail_postcode)[0]
	order.billing_detail_street  = '%s %s' % (shipping_data['Logradouro'],data['billing_detail_complement'])
	order.billing_detail_city    = shipping_data['Localidade']
	order.billing_detail_state   = shipping_data['UF']
	order.billing_detail_country = settings.STORE_COUNTRY
	order.save()
	cart = Cart.objects.from_request(request)
	currency = settings.SHOP_CURRENCY
	cart_items = []
	has_shipping = False
	for item in cart.items.all():
		quantity = len(DeliverableProperty.objects.filter(sku=item.sku))
		if quantity > 0: has_shipping = True
		cart_items.append({
			"name":item.description,
			"sku":item.sku,
			"price":'%.2f' % item.unit_price,
			"currency":currency,
			"quantity":item.quantity
		})
	if has_shipping:
		cart_items.append({
			"name": "Frete via SEDEX",
			"sku":"1",
			"price":'%.2f' % shipping,
			"currency":currency,
			"quantity":1
		})
	price = cart.total_price()+shipping

	if '1' in data['card_pay_option']:
		return paypal_payment(request,cart_items,price,currency)
	elif '2' in data['card_pay_option']:
		return pagseguro_payment(request,cart_items,price,order)

def pagseguro_payment(request,items,price,order):
	server_host = request.get_host()
	payment = pagseguro_api()
	for product in items:
		payment.add_item(id=product['sku'], 
        				 description=product['name'], 
        				 amount=product['price'], 
        				 quantity=product['quantity'])
	# Fixes problems in localhost development environment for PagSeguro checkout
	if 'localhost' in server_host or 'ubuntu' in server_host: server_host = settings.DEFAULT_HOST
	payment.redirect_url = "http://%s/store/execute" % server_host
	response = payment.checkout()
	order.pagseguro_redirect = response.payment_url
	order.save()
	return response.code