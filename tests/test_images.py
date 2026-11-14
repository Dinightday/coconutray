"""Only actual supplied catalog image URLs are used. No image substitution."""
from decimal import Decimal
from unittest.mock import patch

import pytest

from coconutray.tray import IntegrationError, _product, validate_image_url


@pytest.mark.parametrize('host',['images.tcdn.com.br','images1.tcdn.com.br','images2.tcdn.com.br','images3.tcdn.com.br','images4.tcdn.com.br','loja.example'])
def test_documented_image_hosts_preserve_exact_supplied_url(host):
    url=f'https://{host}/img/img_prod/123/foto.jpg?size=400'
    assert validate_image_url(url,'https://loja.example',resolve=False)==url


@pytest.mark.parametrize('url',['http://images.tcdn.com.br/a.jpg','https://images.tcdn.com.br.evil.example/a.jpg','https://127.0.0.1/a.jpg','https://evil.example/a.jpg','https://user:pass@images.tcdn.com.br/a.jpg','https://images.tcdn.com.br:8080/a.jpg','javascript:alert(1)'])
def test_untrusted_or_unsafe_images_are_not_accepted(url):
    with pytest.raises(IntegrationError): validate_image_url(url,'https://loja.example',resolve=False)


def test_catalog_image_flag_does_not_hide_actual_productimage_photo():
    photo='https://images.tcdn.com.br/img/img_prod/123/caderno.jpg'
    result=_product({'id':'12','name':'Caderno','price':'24.90','available':'1','image':'1',
                     'ProductImage':[{'https':photo}]},domain='https://loja.example')
    assert result.image_url==photo and result.price==Decimal('24.90')


def test_missing_or_wrong_source_photo_remains_none():
    row={'id':'12','name':'Caderno','price':'24.90','available':'1'}
    assert _product(row,domain='https://loja.example').image_url is None
    assert _product({**row,'image':'https://other.example/some-product.jpg'},domain='https://loja.example').image_url is None
