class SessionQuitMixin:
    """结束卡文案与 SessionService 入口。确认退出只走 SessionService.handle。"""

    @property
    def session_service(self):
        service = getattr(self, "_session_service", None)
        if service is None:
            from .session_service import SessionService

            service = SessionService(self)
            self._session_service = service
        return service

    def _end_game_tip(self, duration_min):
        if duration_min < 5:
            return "风扇都没转热，主人就结束了？"
        if duration_min < 10:
            return "杂鱼杂鱼~主人你就这水平？"
        if duration_min < 30:
            return "热身一下就结束了？"
        if duration_min < 60:
            return "歇会儿再来，别太累了喵！"
        if duration_min < 120:
            return "沉浸在游戏世界，时间过得飞快喵！"
        if duration_min < 300:
            return "肝到手软了喵！主人不如陪陪咱~"
        if duration_min < 600:
            return "你吃饭了吗？还是说你已经忘了吃饭这件事？"
        if duration_min < 1200:
            return "家里电费都要被你玩光了喵！"
        if duration_min < 1800:
            return "咱都要给你颁发'不眠猫'勋章了！"
        if duration_min < 2400:
            return "主人你还活着喵？你是不是忘了关电脑呀~"
        return "你已经和椅子合为一体，成为传说中的'椅子精'了喵！"
