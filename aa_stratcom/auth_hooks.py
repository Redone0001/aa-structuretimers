from django.utils.translation import gettext_lazy as _

from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

from . import __title__, urls


class TimersMenuItem(MenuItemHook):
    """This class ensures only authorized users will see the menu entry"""

    def __init__(self):
        # setup menu entry for sidebar
        MenuItemHook.__init__(
            self,
            _(__title__),
            "far fa-calendar-times fa-fw",
            "aa_stratcom:timer_list",
            navactive=["aa_stratcom:"],
        )

    def render(self, request):
        if request.user.has_perm("aa_stratcom.basic_access"):
            return MenuItemHook.render(self, request)
        return ""


@hooks.register("menu_item_hook")
def register_menu():
    return TimersMenuItem()


@hooks.register("url_hook")
def register_urls():
    return UrlHook(
        urls,
        "aa_stratcom",
        r"^aa_stratcom/",
        excluded_views=["aa_stratcom.sync_api.timer_sync"],
    )
