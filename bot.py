import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.log import logger

nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)
nonebot.load_plugins("plugins")


if __name__ == "__main__":
    logger.info("Starting NoneBot2 with OneBot V11 websocket client: ws://127.0.0.1:6098")
    nonebot.run()
