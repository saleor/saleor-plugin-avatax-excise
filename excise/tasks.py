import json
import logging

import opentracing
import opentracing.tags

from saleor.celeryconf import app
from saleor.core.taxes import TaxError
from saleor.order.events import external_notification_event
from saleor.order.models import Order
from .utils import (
    AvataxConfiguration,
    api_post_request,
    get_metadata_key,
)

logger = logging.getLogger(__name__)


@app.task(
    autoretry_for=(TaxError,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 5},
)
def api_post_request_task(
    transaction_url, data, config, order_id, commit_url=None
):
    config = AvataxConfiguration(**config)
    order = Order.objects.filter(id=order_id).first()
    if not order:
        msg = (
            "Unable to send the order %s to Avatax Excise. "
            "Order doesn't exist."
        )
        logger.error(msg, order_id)
        return
    if not data.get("TransactionLines"):
        msg = (
            "The order doesn't have any line which should be "
            "sent to Avatax Excise."
        )
        external_notification_event(
            order=order, user=None, message=msg, parameters=None
        )
        return

    with opentracing.global_tracer().start_active_span(
        "avatax_excise.transactions.create"
    ) as scope:
        span = scope.span
        span.set_tag(opentracing.tags.COMPONENT, "tax")
        span.set_tag("service.name", "avatax_excise")

        response = api_post_request(transaction_url, data, config)
    msg = f"Order sent to Avatax Excise. Order ID: {order.token}"
    if not response or "Error" in response.get("Status"):
        errors = response.get("TransactionErrors", [])
        avatax_msg = ""
        for error in errors:
            avatax_msg += error.get("ErrorMessage", "")
        msg = f"Unable to send order to Avatax Excise. {avatax_msg}"
        logger.warning(
            "Unable to send order %s to Avatax Excise. Response %s",
            order.token,
            response,
        )
    else:
        user_tran_id = response.get('UserTranId')
        if config.autocommit and commit_url and user_tran_id:
            commit_url = commit_url.format(user_tran_id)
            response = api_post_request(
                commit_url,
                {},
                config,
            )
            errors = response.get("TransactionErrors", [])
            avatax_msg = ""
            for error in errors:
                avatax_msg += error.get("ErrorMessage", "")
            msg = f"Order committed to Avatax Excise. Order ID: {order.token}"
            if not response or "Error" in response.get("Status"):
                msg = f"Unable to commit order to Avatax Excise. {avatax_msg}"
                logger.warning(
                    "Unable to commit order %s to Avatax Excise. Response %s",
                    order.token,
                    response,
                )

    tax_item = {
        get_metadata_key("itemized_taxes"): json.dumps(
            response.get("TransactionTaxes")
        )
    }
    order.store_value_in_metadata(items=tax_item)
    order.save()

    external_notification_event(
        order=order, user=None, message=msg, parameters=None
    )
    if not response or "Error" in response.get("Status"):
        raise TaxError
