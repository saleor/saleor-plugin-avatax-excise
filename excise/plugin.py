import json
import logging
from dataclasses import asdict
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Iterable, List, Optional
from urllib.parse import urljoin

import opentracing
import opentracing.tags
from django.core.exceptions import ValidationError
from prices import Money, TaxedMoney

from saleor.checkout.models import Checkout
from saleor.core.prices import quantize_price
from saleor.core.taxes import TaxError, zero_taxed_money
from saleor.discount import DiscountInfo
from saleor.plugins.base_plugin import ConfigurationTypeField
from saleor.plugins.error_codes import PluginErrorCode
from saleor.plugins.avatax import _validate_checkout
from saleor.plugins.avatax.plugin import AvataxPlugin
from .utils import (
    AvataxConfiguration,
    api_get_request,
    api_post_request,
    generate_request_data_from_checkout,
    get_api_url,
    get_checkout_tax_data,
    get_order_request_data,
    TRANSACTION_TYPE,
    process_checkout_metadata,
)
from .tasks import api_post_request_task

if TYPE_CHECKING:
    # flake8: noqa
    from saleor.account.models import Address
    from saleor.channel.models import Channel
    from saleor.checkout import CheckoutLineInfo
    from saleor.checkout.models import CheckoutLine
    from saleor.order.models import Order
    from saleor.plugins.models import PluginConfiguration

logger = logging.getLogger(__name__)


class AvataxExcisePlugin(AvataxPlugin):
    PLUGIN_NAME = "Avalara Excise"
    PLUGIN_ID = "mirumee.taxes.avalara_excise"

    DEFAULT_CONFIGURATION = [
        {"name": "Username or account", "value": None},
        {"name": "Password or license", "value": None},
        {"name": "Use sandbox", "value": True},
        {"name": "Company name", "value": ""},
        {"name": "Autocommit", "value": False},
        {"name": "Freight tax code", "value": "FR020100"},
    ]
    CONFIG_STRUCTURE = {
        "Username or account": {
            "type": ConfigurationTypeField.STRING,
            "help_text": "Provide user details",
            "label": "Username",
        },
        "Password or license": {
            "type": ConfigurationTypeField.PASSWORD,
            "help_text": "Provide password details",
            "label": "Password",
        },
        "Use sandbox": {
            "type": ConfigurationTypeField.BOOLEAN,
            "help_text": "Determines if Saleor should use Avatax Excise sandbox API.",
            "label": "Use sandbox",
        },
        "Company name": {
            "type": ConfigurationTypeField.STRING,
            "help_text": "Avalara company ID.",
            "label": "Company ID",
        },
        "Autocommit": {
            "type": ConfigurationTypeField.BOOLEAN,
            "help_text": "Determines, if order transactions sent to Avalara "
            "Excise should be committed by default.",
            "label": "Autocommit",
        },
        "Freight tax code": {
            "type": ConfigurationTypeField.STRING,
            "help_text": "Avalara Tax Code to use for shipping. See "
            "https://taxcode.avatax.avalara.com/tree?tree=freight-and-freight-related-charges&tab=interactive",
            "label": "Freight Tax Code",
        },
    }

    def __init__(self, *args, **kwargs):
        super(AvataxPlugin, self).__init__(*args, **kwargs)
        # Convert to dict to easier take config elements
        configuration = {item["name"]: item["value"] for item in self.configuration}

        self.config = AvataxConfiguration(
            username_or_account=configuration["Username or account"],
            password_or_license=configuration["Password or license"],
            use_sandbox=configuration["Use sandbox"],
            company_name=configuration["Company name"],
            autocommit=configuration["Autocommit"],
            freight_tax_code=configuration["Freight tax code"],
        )

    @classmethod
    def validate_authentication(cls, plugin_configuration: "PluginConfiguration"):
        conf = {
            data["name"]: data["value"] for data in plugin_configuration.configuration
        }
        url = urljoin(get_api_url(conf["Use sandbox"]), "utilities/ping")
        response = api_get_request(
            url,
            username_or_account=conf["Username or account"],
            password_or_license=conf["Password or license"],
        )

        if not response.get("authenticated"):
            raise ValidationError(
                "Authentication failed. Please check provided data.",
                code=PluginErrorCode.PLUGIN_MISCONFIGURED.value,
            )

    @classmethod
    def validate_plugin_configuration(cls, plugin_configuration: "PluginConfiguration"):
        """Validate if provided configuration is correct."""
        missing_fields = []
        configuration = plugin_configuration.configuration
        configuration = {item["name"]: item["value"] for item in configuration}
        if not configuration["Username or account"]:
            missing_fields.append("Username or account")
        if not configuration["Password or license"]:
            missing_fields.append("Password or license")

        if plugin_configuration.active:
            if missing_fields:
                error_msg = (
                    "To enable a plugin, you need to provide values for the "
                    "following fields: "
                )
                raise ValidationError(
                    error_msg + ", ".join(missing_fields),
                    code=PluginErrorCode.PLUGIN_MISCONFIGURED.value,
                )

            cls.validate_authentication(plugin_configuration)

    def calculate_checkout_total(
        self,
        checkout_info: "CheckoutInfo",
        lines: Iterable["CheckoutLineInfo"],
        address: Optional["Address"],
        discounts: Iterable[DiscountInfo],
        previous_value: TaxedMoney,
    ) -> TaxedMoney:
        if self._skip_plugin(previous_value):
            logger.debug("Skip Plugin in Calculate Checkout Total")
            return previous_value
        checkout_total = previous_value

        if not _validate_checkout(checkout_info, lines):
            logger.debug("Checkout Invalid in Calculate Checkout Total")
            return checkout_total

        checkout = checkout_info.checkout  ## change this
        response = get_checkout_tax_data(checkout_info, lines, discounts, self.config)
        if not response or "Errors found" in response["Status"]:
            return checkout_total

        currency = checkout.currency

        tax = Money(Decimal(response.get("TotalTaxAmount", 0.0)), currency)
        net = checkout_total.net
        total_gross = net + tax
        taxed_total = quantize_price(TaxedMoney(net=net, gross=total_gross), currency)
        total = self._append_prices_of_not_taxed_lines(
            taxed_total,
            lines,
            checkout_info.channel,
            discounts,
        )

        voucher_value = checkout.discount
        if voucher_value:
            total -= voucher_value

        return max(total, zero_taxed_money(total.currency))

    # def calculate_checkout_subtotal(
    #     self,
    #     checkout: "Checkout",
    #     lines: Iterable["CheckoutLine"],
    #     discounts: Iterable[DiscountInfo],
    #     previous_value: TaxedMoney,
    # ) -> TaxedMoney:
    #     return previous_value

    def calculate_checkout_shipping(
        self,
        checkout_info: "CheckoutInfo",
        lines: List["CheckoutLineInfo"],
        address: Optional["Address"],
        discounts: List["DiscountInfo"],
        previous_value: TaxedMoney,
    ) -> TaxedMoney:
        return previous_value

    def preprocess_order_creation(
        self,
        checkout_info: "CheckoutInfo",
        discounts: List["DiscountInfo"],
        lines: Optional[Iterable["CheckoutLineInfo"]],
        previous_value: Any,
    ):
        """Ensure all the data is correct and we can proceed with creation of order.

        Raise an error when can't receive taxes.
        """

        if self._skip_plugin(previous_value):
            return previous_value

        data = generate_request_data_from_checkout(
            checkout_info,
            lines_info=lines,
            config=self.config,
            transaction_type=TRANSACTION_TYPE,
            discounts=discounts,
        )
        if not data.TransactionLines:
            return previous_value
        transaction_url = urljoin(
            get_api_url(self.config.use_sandbox), "AvaTaxExcise/transactions/create"
        )
        with opentracing.global_tracer().start_active_span(
            "avatax_excise.transactions.create"
        ) as scope:
            span = scope.span
            span.set_tag(opentracing.tags.COMPONENT, "tax")
            span.set_tag("service.name", "avatax_excise")
            response = api_post_request(transaction_url, data, self.config)
        if not response or response.get("Status") != "Success":
            transaction_errors = response.get("TransactionErrors")
            customer_msg = ""
            if isinstance(transaction_errors, list):
                for error in transaction_errors:
                    error_message = error.get("ErrorMessage")
                    if error_message:
                        customer_msg += error_message
                    error_code = response.get("ErrorCode", "")
                    logger.warning(
                        "Unable to calculate taxes for checkout %s, error_code: %s, "
                        "error_msg: %s",
                        checkout_info.checkout.token,
                        error_code,
                        error_message,
                    )
                    if error_code == "-1003":
                        raise ValidationError(error_message)
            raise TaxError(customer_msg)
        return previous_value

    def order_created(self, order: "Order", previous_value: Any) -> Any:
        if not self.active:
            return previous_value
        request_data = get_order_request_data(order)
        transaction_url = urljoin(
            get_api_url(self.config.use_sandbox),
            "AvaTaxExcise/transactions/create",
        )
        api_post_request_task.delay(
            transaction_url, asdict(request_data), asdict(self.config), order.id
        )

        return previous_value

    def calculate_checkout_line_total(
        self,
        checkout_info: "CheckoutInfo",
        lines: Iterable["CheckoutLineInfo"],
        checkout_line_info: "CheckoutLineInfo",
        address: Optional["Address"],
        discounts: Iterable["DiscountInfo"],
        previous_value: TaxedMoney,
    ) -> TaxedMoney:
        if self._skip_plugin(previous_value):
            return previous_value

        base_total = previous_value
        if not checkout_line_info.product.charge_taxes:
            return base_total

        checkout = checkout_info.checkout

        if not _validate_checkout(checkout_info, lines):
            return base_total

        taxes_data = get_checkout_tax_data(checkout_info, lines, discounts, self.config)

        if not taxes_data or "Error" in taxes_data["Status"]:
            return base_total

        tax_meta = json.dumps(taxes_data["TransactionTaxes"])
        process_checkout_metadata(tax_meta, checkout)

        line_tax_total = Decimal(0)

        for line in taxes_data.get("TransactionTaxes", []):
            if line.get("InvoiceLine") == checkout_line_info.line.id:
                line_tax_total += Decimal(line.get("TaxAmount", 0.0))

        if not line_tax_total > 0:
            return base_total

        currency = checkout.currency
        tax = Decimal(line_tax_total)
        line_net = Decimal(base_total.net.amount)
        line_gross = Money(amount=line_net + tax, currency=currency)
        line_net = Money(amount=line_net, currency=currency)

        return quantize_price(TaxedMoney(net=line_net, gross=line_gross), currency)

    def calculate_checkout_line_unit_price(
        self,
        checkout_info: "CheckoutInfo",
        lines: List["CheckoutLineInfo"],
        checkout_line_info: "CheckoutLineInfo",
        address: Optional["Address"],
        discounts: Iterable["DiscountInfo"],
        previous_value: TaxedMoney,
    ):
        return previous_value

    def get_checkout_line_tax_rate(
        self,
        checkout_info: "CheckoutInfo",
        lines: List["CheckoutLineInfo"],
        checkout_line_info: "CheckoutLineInfo",
        address: Optional["Address"],
        discounts: Iterable["DiscountInfo"],
        previous_value: Decimal,
    ) -> Decimal:
        return previous_value

    def get_checkout_shipping_tax_rate(
        self,
        checkout_info: "CheckoutInfo",
        lines: Iterable["CheckoutLineInfo"],
        address: Optional["Address"],
        discounts: Iterable["DiscountInfo"],
        previous_value: Decimal,
    ):
        return previous_value
