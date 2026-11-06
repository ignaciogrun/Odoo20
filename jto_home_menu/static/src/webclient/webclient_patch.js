import { patch } from "@web/core/utils/patch";
import { NavBar } from "@web/webclient/navbar/navbar";
import { WebClient } from "@web/webclient/webclient";
import { HOME_MENU_ACTION, homeMenuState } from "../home_menu/home_menu";

function openHomeMenu(actionService) {
    return actionService.doAction(HOME_MENU_ACTION, { clearBreadcrumbs: true });
}

// Land on the home menu instead of the first app when the URL has no action
// (e.g. right after login, or when opening /odoo).
patch(WebClient.prototype, {
    _loadDefaultApp() {
        return openHomeMenu(this.actionService);
    },
});

patch(NavBar.prototype, {
    get currentApp() {
        return homeMenuState.active ? null : super.currentApp;
    },
    onHomeMenuClick() {
        return openHomeMenu(this.actionService);
    },
    // Phone sidebar: "All Apps" opens the home menu instead of a list.
    onAllAppsBtnClick() {
        this._closeAppMenuSidebar();
        return openHomeMenu(this.actionService);
    },
});
