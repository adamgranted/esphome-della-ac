import esphome.codegen as cg
from esphome.components import climate, uart
import esphome.config_validation as cv

DEPENDENCIES = ["uart"]

della_ns = cg.esphome_ns.namespace("della_ac")
DellaAC = della_ns.class_("DellaAC", climate.Climate, cg.Component, uart.UARTDevice)

CONFIG_SCHEMA = (
    climate.climate_schema(DellaAC)
    .extend(uart.UART_DEVICE_SCHEMA)
    .extend(cv.COMPONENT_SCHEMA)
)


async def to_code(config):
    var = await climate.new_climate(config)
    await cg.register_component(var, config)
    await uart.register_uart_device(var, config)
