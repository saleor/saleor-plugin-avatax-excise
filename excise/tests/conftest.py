import pytest
from django.core.management.color import no_style
from django.db import connection
from saleor.account.models import Address
from saleor.checkout.models import CheckoutLine
from saleor.plugins.models import PluginConfiguration
from saleor.product.models import ProductType
from saleor.warehouse.models import Warehouse

from ..plugin import AvataxExcisePlugin


@pytest.fixture(scope="module")
def vcr_config():
    return {
        "filter_headers": [("Authorization", "Basic Og==")],
    }


@pytest.fixture
def plugin_configuration(db, channel_USD):
    def set_configuration(
        username="test",
        password="test",
        company_id="test",
        sandbox=True,
        channel=None,
    ):
        channel = channel or channel_USD
        data = {
            "active": True,
            "name": AvataxExcisePlugin.PLUGIN_NAME,
            "channel": channel,
            "configuration": [
                {"name": "Username or account", "value": username},
                {"name": "Password or license", "value": password},
                {"name": "Use sandbox", "value": sandbox},
                {"name": "Company name", "value": company_id},
                {"name": "Autocommit", "value": False},
                {"name": "Shipping Product Code", "value": "TAXFREIGHT"},
            ],
        }
        configuration = PluginConfiguration.objects.create(
            identifier=AvataxExcisePlugin.PLUGIN_ID, **data
        )
        return configuration

    return set_configuration


@pytest.fixture
def address_usa_va():
    return Address.objects.create(
        first_name="John",
        last_name="Doe",
        street_address_1="1100 Congress Ave",
        city="Richmond",
        postal_code="23226",
        country_area="VA",
        country="US",
        phone="",
    )


@pytest.fixture
def cigar_product_type():
    return ProductType.objects.create(
        name="Cigar",
        private_metadata={
            "mirumee.taxes.avalara_excise:UnitOfMeasure": "PAC",
            "mirumee.taxes.avalara_excise:UnitQuantityUnitOfMeasure": "EA",
        },
    )


@pytest.fixture
def warehouse(address_usa_va, shipping_zone):
    warehouse = Warehouse.objects.create(
        address=address_usa_va,
        name="Example Warehouse",
        slug="example-warehouse",
        email="test@example.com",
    )
    warehouse.shipping_zones.add(shipping_zone)
    warehouse.save()
    return warehouse


@pytest.fixture
def reset_sequences():
    sequence_sql = connection.ops.sequence_reset_sql(
        no_style(), [CheckoutLine]
    )
    with connection.cursor() as cursor:
        for sql in sequence_sql:
            cursor.execute(sql)
