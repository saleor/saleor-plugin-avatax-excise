# Saleor Plugin Avatax Excise

### Mappings

| Saleor                                                                                                  | Avalara Excise            | Explanation                                                                    |
| ------------------------------------------------------------------------------------------------------- | ------------------------- | ------------------------------------------------------------------------------ |
| n/a                                                                                                     | TransactionType           | hardcoded "RETAIL"                                                             |
| n/a                                                                                                     | TitleTransferCode         | hardcoded "DEST", Definition of where the title transfer takes place           |
| Site.settings.include_taxes_in_prices                                                                   | TaxIncluded               |                                                                                |
| variant.sku                                                                                             | ProductCode               | ProductCode that ATE recognizes                                                |
| variant.price.amount                                                                                    | UnitPrice                 | Sale Price Per Unit                                                            |
| variant.product.product_type.private_metadata["UnitOfMeasure"]                                          | UnitOfMeasure             |                                                                                |
| variant.cost_price.amount                                                                               | AlternateUnitPrice        | wholesale cost (or other value needed as excise tax basis for tax calculation) |
| variant.privateMetadata.mirumee.taxes.avalara_excise:UnitQuantity                                       | UnitQuantity              | item count within the package                                                  |
| variant.product.product_type.private_metadata["mirumee.taxes.avalara_excise:UnitQuantityUnitOfMeasure"] | UnitQuantityUnitOfMeasure |                                                                                |
| variant.product.product_type.private_metadata["mirumee.taxes.avalara_excise:UnitOfMeasure"]             | UnitOfMeasure             |                                                                                |
| shipping_address.country.alpha3                                                                         | DestinationCountryCode    |                                                                                |
| shipping_address.country_area                                                                           | DestinationJurisdiction   | within the US this is a state                                                  |
| shipping_address.street_address_1                                                                       | DestinationAddress1       |                                                                                |
| shipping_address.street_address_2                                                                       | DestinationAddress2       |                                                                                |
| shipping_address.city_area                                                                              | DestinationCounty         |                                                                                |
| shipping_address.city                                                                                   | DestinationCity           |                                                                                |
| shipping_address.postal_code                                                                            | DestinationPostalCode     |                                                                                |
| shipping_address.country.alpha3                                                                         | SaleCountryCode           |                                                                                |
| shipping_address.street_address_1                                                                       | SaleAddress1              |                                                                                |
| shipping_address.street_address_2                                                                       | SaleAddress2              |                                                                                |
| shipping_address.country_area                                                                           | SaleJurisdiction          | within the US this is a state                                                  |
| shipping_address.city_area                                                                              | SaleCounty                |                                                                                |
| shipping_address.city                                                                                   | SaleCity                  |                                                                                |
| shipping_address.postal_code                                                                            | SalePostalCode            |                                                                                |
| warehouse.address.country.alpha3                                                                        | OriginCountryCode         | Location of the warehouse                                                      |
| warehouse.address.country_area                                                                          | OriginJurisdiction        | within the US this is a state                                                  |
| warehouse.address.city_area                                                                             | OriginCounty              |                                                                                |
| warehouse.address.city                                                                                  | OriginCity                |                                                                                |
| warehouse.address.postal_code                                                                           | OriginPostalCode          |                                                                                |
| warehouse.address.street_address_1                                                                      | OriginAddress1            |                                                                                |
| warehouse.address.street_address_2                                                                      | OriginAddress2            |                                                                                |
| variant.private_metadata["mirumee.taxes.avalara_excise:CustomString1"]                                  | CustomString1             |                                                                                |
| variant.private_metadata["mirumee.taxes.avalara_excise:CustomString2"]                                  | CustomString2             |                                                                                |
| variant.private_metadata["mirumee.taxes.avalara_excise:CustomString3"]                                  | CustomString3             |                                                                                |
| variant.private_metadata["mirumee.taxes.avalara_excise:CustomNumeric1"]                                 | CustomNumeric1            |                                                                                |
| variant.private_metadata["mirumee.taxes.avalara_excise:CustomNumeric2"]                                 | CustomNumeric2            |                                                                                |
| variant.private_metadata["mirumee.taxes.avalara_excise:CustomNumeric3"]                                 | CustomNumeric3            |                                                                                |

### Checkout Mappings

| Saleor                           | Avalara Excise | Explanation                                                                               |
| -------------------------------- | -------------- | ----------------------------------------------------------------------------------------- |
| Checkout.last_change             | EffectiveDate  |                                                                                           |
| Checkout.last_change             | InvoiceDate    |                                                                                           |
| CheckoutLine.id                  | InvoiceLine    | Important value indicating which invoice line corresponds to the tax line in the response |
| base_checkout_line_total         | LineAmount     |                                                                                           |
| Checkout.line_info.line.quantity | BilledUnits    |                                                                                           |
|                                  |

### Order Mappings

| Saleor        | Avalara Excise | Explanation                                                                               |
| ------------- | -------------- | ----------------------------------------------------------------------------------------- |
| Order.id      | InvoiceNumber  |                                                                                           |
| order.created | EffectiveDate  |                                                                                           |
| order.created | InvoiceDate    |                                                                                           |
| order.line.id | InvoiceLine    | Important value indicating which invoice line corresponds to the tax line in the response |
