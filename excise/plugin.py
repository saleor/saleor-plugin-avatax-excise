import logging
from dataclasses import asdict
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin

import opentracing
import opentracing.tags
from django.core.exceptions import ValidationError
from prices import Money, TaxedMoney
from saleor.checkout import base_calculations
from saleor.checkout.interface import CheckoutTaxedPricesData
from saleor.core.prices import quantize_price
from saleor.core.taxes import (TaxError, charge_taxes_on_shipping,
                               zero_taxed_money)
from saleor.discount import DiscountInfo
from saleor.order.interface import OrderTaxedPricesData
from saleor.plugins.avatax import (_validate_checkout, _validate_order,
                                   api_get_request)
from saleor.plugins.avatax.plugin import AvataxPlugin
from saleor.plugins.base_plugin import ConfigurationTypeField
from saleor.plugins.error_codes import PluginErrorCode

from .tasks import api_post_request_task
from .utils import (TRANSACTION_TYPE, AvataxConfiguration, api_post_request,
                    generate_request_data_from_checkout, get_api_url,
                    get_checkout_tax_data, get_order_request_data,
                    get_order_tax_data, process_checkout_metadata)

if TYPE_CHECKING:
    # flake8: noqa
    from saleor.account.models import Address
    from saleor.channel.models import Channel
    from saleor.checkout.fetch import CheckoutInfo, CheckoutLineInfo
    from saleor.order.models import Order, OrderLine
    from saleor.plugins.models import PluginConfiguration
    from saleor.product.models import Product, ProductVariant

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
        {"name": "Shipping Product Code", "value": "TAXFREIGHT"},
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
            "help_text": "Determines if Saleor should use Avatax "
            "Excise sandbox API.",
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
        "Shipping Product Code": {
            "type": ConfigurationTypeField.STRING,
            "help_text": "Avalara Excise Product Code used to represent "
            "shipping. This Product should set the Avatax Tax Code to "
            "FR020000 or other freight tax code. See "
            "https://taxcode.avatax.avalara.com/tree"
            "?tree=freight-and-freight-related-charges&tab=interactive",
            "label": "Shipping Product Code",
        },
    }

    def __init__(self, *args, **kwargs):
        super(AvataxPlugin, self).__init__(*args, **kwargs)
        # Convert to dict to easier take config elements
        configuration = {
            item["name"]: item["value"] for item in self.configuration
        }

        self.config = AvataxConfiguration(
            username_or_account=configuration["Username or account"],
            password_or_license=configuration["Password or license"],
            use_sandbox=configuration["Use sandbox"],
            company_name=configuration["Company name"],
            autocommit=configuration["Autocommit"],
            shipping_product_code=configuration["Shipping Product Code"],
        )

    @classmethod
    def validate_authentication(
        cls, plugin_configuration: "PluginConfiguration"
    ):
        conf = {
            data["name"]: data["value"]
            for data in plugin_configuration.configuration
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
    def validate_plugin_configuration(
        cls, plugin_configuration: "PluginConfiguration"
    ):
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

        taxes_data = get_checkout_tax_data(
            checkout_info, lines, discounts, self.config
        )
        if not taxes_data or "Errors found" in taxes_data["Status"]:
            return previous_value
        process_checkout_metadata(taxes_data, checkout_info.checkout)

        checkout = checkout_info.checkout
        currency = checkout.currency
        tax = Money(Decimal(taxes_data.get("TotalTaxAmount", 0.0)), currency)
        net = checkout_total.net
        gross = net + tax
        taxed_total = quantize_price(
            TaxedMoney(net=net, gross=gross), currency
        )
        total = self._append_prices_of_not_taxed_lines(
            taxed_total,
            lines,
            checkout_info.channel,
            discounts,
        )

        return max(total, zero_taxed_money(total.currency))

    def _append_prices_of_not_taxed_lines(
        self,
        price: TaxedMoney,
        lines: Iterable["CheckoutLineInfo"],
        channel: "Channel",
        discounts: Iterable[DiscountInfo],
    ):
        for line_info in lines:
            if line_info.product.charge_taxes:
                continue
            prices_data = base_calculations.base_checkout_line_total(
                line_info,
                channel,
                discounts,
            )
            price_with_discounts = prices_data.price_with_discounts
            price.gross.amount += price_with_discounts.gross.amount
            price.net.amount += price_with_discounts.net.amount
        return price

    def _calculate_checkout_shipping(
        self, currency: str, lines: List[Dict], shipping_price: TaxedMoney
    ) -> TaxedMoney:
        shipping_tax = Decimal(0.0)
        shipping_net = shipping_price.net.amount
        for line in lines:
            if line["InvoiceLine"] == 0:
                shipping_net += Decimal(line["TaxAmount"])
                shipping_tax += Decimal(line["TaxAmount"])

        shipping_gross = Money(
            amount=shipping_net + shipping_tax, currency=currency
        )
        shipping_net = Money(amount=shipping_net, currency=currency)
        return TaxedMoney(net=shipping_net, gross=shipping_gross)

    def calculate_checkout_shipping(
        self,
        checkout_info: "CheckoutInfo",
        lines: List["CheckoutLineInfo"],
        address: Optional["Address"],
        discounts: List["DiscountInfo"],
        previous_value: TaxedMoney,
    ) -> TaxedMoney:
        if not charge_taxes_on_shipping():
            return previous_value

        if self._skip_plugin(previous_value):
            return previous_value

        if not _validate_checkout(checkout_info, lines):
            return previous_value

        taxes_data = get_checkout_tax_data(
            checkout_info, lines, discounts, self.config
        )
        if not taxes_data or "error" in taxes_data:
            return previous_value
        process_checkout_metadata(taxes_data, checkout_info.checkout)

        tax_lines = taxes_data.get("TransactionTaxes", [])
        if not tax_lines:
            return previous_value

        currency = checkout_info.checkout.currency
        return self._calculate_checkout_shipping(
            currency, tax_lines, previous_value
        )

    def preprocess_order_creation(
        self,
        checkout_info: "CheckoutInfo",
        discounts: List["DiscountInfo"],
        lines: Optional[Iterable["CheckoutLineInfo"]],
        previous_value: Any,
    ):
        """
        Ensure all the data is correct and we can proceed with creation of
        order. Raise an error when can't receive taxes.
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
            get_api_url(self.config.use_sandbox),
            "AvaTaxExcise/transactions/create",
        )
        with opentracing.global_tracer().start_active_span(
            "avatax_excise.transactions.create"
        ) as scope:
            span = scope.span
            span.set_tag(opentracing.tags.COMPONENT, "tax")
            span.set_tag("service.name", "avatax_excise")
            taxes_data = api_post_request(transaction_url, data, self.config)
        if not taxes_data or taxes_data.get("Status") != "Success":
            transaction_errors = taxes_data.get("TransactionErrors")
            customer_msg = ""
            if isinstance(transaction_errors, list):
                for error in transaction_errors:
                    error_message = error.get("ErrorMessage")
                    if error_message:
                        customer_msg += error_message
                    error_code = taxes_data.get("ErrorCode", "")
                    logger.warning(
                        "Unable to calculate taxes for checkout %s"
                        "error_code: %s error_msg: %s",
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

        request_data = get_order_request_data(order, self.config)
        base_url = get_api_url(self.config.use_sandbox)
        transaction_url = urljoin(
            base_url,
            "AvaTaxExcise/transactions/create",
        )
        commit_url = urljoin(
            base_url,
            "AvaTaxExcise/transactions/{}/commit",
        )

        api_post_request_task.delay(
            transaction_url,
            asdict(request_data),
            asdict(self.config),
            order.id,
            commit_url,
        )

        return previous_value

    def order_confirmed(self, order: "Order", previous_value: Any) -> Any:
        return previous_value

    def order_updated(self, order: "Order", previous_value: Any) -> Any:
        return previous_value

    def calculate_checkout_line_total(
        self,
        checkout_info: "CheckoutInfo",
        lines: Iterable["CheckoutLineInfo"],
        checkout_line_info: "CheckoutLineInfo",
        address: Optional["Address"],
        discounts: Iterable["DiscountInfo"],
        previous_value: CheckoutTaxedPricesData,
    ) -> CheckoutTaxedPricesData:
        if self._skip_plugin(previous_value):
            return previous_value

        if not checkout_line_info.product.charge_taxes:
            return previous_value

        if not _validate_checkout(checkout_info, lines):
            return previous_value

        taxes_data = get_checkout_tax_data(
            checkout_info, lines, discounts, self.config
        )
        if not taxes_data or "Errors found" in taxes_data["Status"]:
            return previous_value
        process_checkout_metadata(taxes_data, checkout_info.checkout)
        return self._calculate_checkout_line_total_price(
            taxes_data, checkout_line_info.line.id, previous_value
        )

    @staticmethod
    def _calculate_checkout_line_total_price(
        taxes_data: Dict[str, Any],
        line_id: str,
        previous_value: CheckoutTaxedPricesData,
    ) -> CheckoutTaxedPricesData:
        if not taxes_data or "error" in taxes_data:
            return previous_value

        tax = Decimal("0.00")
        currency = ""
        for line in taxes_data.get("TransactionTaxes", []):
            if line.get("InvoiceLine") == line_id:
                tax += Decimal(line.get("TaxAmount", "0.00"))
                if not currency:
                    currency = line.get("Currency")

        if tax > 0 and currency:
            net = Decimal(previous_value.price_with_discounts.net.amount)

            line_net = Money(amount=net, currency=currency)
            line_gross = Money(amount=net + tax, currency=currency)
            price_with_discounts = TaxedMoney(net=line_net, gross=line_gross)
            return CheckoutTaxedPricesData(
                price_with_discounts=price_with_discounts,
                price_with_sale=price_with_discounts,
                undiscounted_price=previous_value.undiscounted_price
            )

        return previous_value

    def calculate_order_line_total(
        self,
        order: "Order",
        order_line: "OrderLine",
        variant: "ProductVariant",
        product: "Product",
        previous_value: OrderTaxedPricesData,
    ) -> OrderTaxedPricesData:
        if self._skip_plugin(previous_value):
            return previous_value

        if not product.charge_taxes:
            return previous_value

        if not _validate_order(order):
            zero_money = zero_taxed_money(order.currency)
            return OrderTaxedPricesData(
                price_with_discounts=zero_money,
                undiscounted_price=zero_money
            )

        taxes_data = self._get_order_tax_data(order, previous_value)
        return self._calculate_order_line_total_price(
            taxes_data, order_line.id, previous_value
        )

    @staticmethod
    def _calculate_order_line_total_price(
        taxes_data: Dict[str, Any],
        line_id: str,
        previous_value: OrderTaxedPricesData,
    ) -> OrderTaxedPricesData:
        if not taxes_data or "error" in taxes_data:
            return previous_value

        tax = Decimal("0.00")
        currency = ""
        for line in taxes_data.get("TransactionTaxes", []):
            if line.get("InvoiceLine") == line_id:
                tax += Decimal(line.get("TaxAmount", "0.00"))
                if not currency:
                    currency = line.get("Currency")

        if tax > 0 and currency:
            net = Decimal(previous_value.price_with_discounts.net.amount)

            line_net = Money(amount=net, currency=currency)
            line_gross = Money(amount=net + tax, currency=currency)
            price_with_discounts = TaxedMoney(net=line_net, gross=line_gross)
            return OrderTaxedPricesData(
                price_with_discounts=price_with_discounts,
                undiscounted_price=previous_value.undiscounted_price
            )

        return previous_value

    def calculate_checkout_line_unit_price(
        self,
        checkout_info: "CheckoutInfo",
        lines: List["CheckoutLineInfo"],
        checkout_line_info: "CheckoutLineInfo",
        address: Optional["Address"],
        discounts: Iterable["DiscountInfo"],
        previous_value: CheckoutTaxedPricesData,
    ) -> CheckoutTaxedPricesData:
        return previous_value

    def calculate_order_line_unit(
        self,
        order: "Order",
        order_line: "OrderLine",
        variant: "ProductVariant",
        product: "Product",
        previous_value: OrderTaxedPricesData,
    ) -> OrderTaxedPricesData:
        if not variant or (variant and not product.charge_taxes):
            return previous_value

        quantity = order_line.quantity
        taxes_data = self._get_order_tax_data(order, previous_value)
        default_total = OrderTaxedPricesData(
            price_with_discounts=previous_value.price_with_discounts * quantity,
            undiscounted_price=previous_value.undiscounted_price * quantity,
        )
        taxed_total_prices_data = self._calculate_order_line_total_price(
            taxes_data, order_line.id, default_total
        )
        return OrderTaxedPricesData(
            undiscounted_price=taxed_total_prices_data.undiscounted_price / quantity,
            price_with_discounts=taxed_total_prices_data.price_with_discounts
            / quantity,
        )

    def calculate_order_shipping(
        self, order: "Order", previous_value: TaxedMoney
    ) -> TaxedMoney:
        if self._skip_plugin(previous_value):
            return previous_value

        if not charge_taxes_on_shipping():
            return previous_value

        if not _validate_order(order):
            return zero_taxed_money(order.total.currency)

        taxes_data = get_order_tax_data(order, self.config, False)
        tax_lines = taxes_data.get("TransactionTaxes", [])
        if not tax_lines:
            return previous_value

        currency = order.currency
        return self._calculate_checkout_shipping(
            currency, tax_lines, previous_value
        )

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

    def _get_checkout_tax_data(
        self,
        checkout_info: "CheckoutInfo",
        lines_info: Iterable["CheckoutLineInfo"],
        discounts: Iterable[DiscountInfo],
        previous_value: Decimal,
    ):
        if self._skip_plugin(previous_value):
            return None

        valid = _validate_checkout(checkout_info, lines_info)
        if not valid:
            return None

        taxes_data = get_checkout_tax_data(
            checkout_info, lines_info, discounts, self.config
        )
        if not taxes_data or "error" in taxes_data:
            return None

        return taxes_data

    def _get_order_tax_data(self, order: "Order", previous_value: Decimal):
        if self._skip_plugin(previous_value):
            return None

        valid = _validate_order(order)
        if not valid:
            return None

        taxes_data = get_order_tax_data(order, self.config, False)
        if not taxes_data or "error" in taxes_data:
            return None

        return taxes_data
