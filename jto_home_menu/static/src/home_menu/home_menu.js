import { Component, onMounted, onWillUnmount, proxy, signal, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useAutofocus, useService } from "@web/core/utils/hooks";
import { fuzzyLookup } from "@web/core/utils/search";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

export const HOME_MENU_ACTION = "jto_home_menu.action_home_menu";

/**
 * Shared flag read by the NavBar patch: while the home menu is displayed the
 * navbar hides the current app's brand and sections.
 */
export const homeMenuState = { active: false };

export class HomeMenu extends Component {
    static template = "jto_home_menu.HomeMenu";
    props = useProps({ ...standardActionServiceProps });
    searchRef = signal.ref();

    setup() {
        this.menuService = useService("menu");
        this.state = proxy({ query: "" });
        useAutofocus({ ref: this.searchRef });

        onMounted(() => {
            homeMenuState.active = true;
            this.env.bus.trigger("MENUS:APP-CHANGED");
        });
        onWillUnmount(() => {
            homeMenuState.active = false;
            this.env.bus.trigger("MENUS:APP-CHANGED");
        });
    }

    get greeting() {
        const hour = new Date().getHours();
        const name = user.name ? user.name.split(" ")[0] : "";
        if (hour < 12) {
            return name ? _t("Good morning, %s", name) : _t("Good morning");
        }
        if (hour < 19) {
            return name ? _t("Good afternoon, %s", name) : _t("Good afternoon");
        }
        return name ? _t("Good evening, %s", name) : _t("Good evening");
    }

    get apps() {
        return this.menuService.getApps().map((app) => {
            const tile = { id: app.id, name: app.name, xmlid: app.xmlid, menu: app };
            if (app.webIconData) {
                tile.image = app.webIconData;
            } else if (app.webIcon) {
                const [iconClass, color, background] = app.webIcon.split(",");
                Object.assign(tile, { iconClass, color, background });
            } else {
                tile.image = "/web/static/img/default_icon_app.png";
            }
            tile.href = `/odoo/${app.actionPath || "action-" + app.actionID}`;
            return tile;
        });
    }

    get visibleApps() {
        const query = this.state.query.trim();
        return query ? fuzzyLookup(query, this.apps, (app) => app.name) : this.apps;
    }

    onSearchInput(ev) {
        this.state.query = ev.target.value;
    }

    onSearchKeydown(ev) {
        if (ev.key === "Enter") {
            const [first] = this.visibleApps;
            if (first) {
                ev.preventDefault();
                this.openApp(first);
            }
        } else if (ev.key === "Escape" && this.state.query) {
            ev.preventDefault();
            ev.target.value = "";
            this.state.query = "";
        }
    }

    openApp(tile) {
        this.menuService.selectMenu(tile.menu);
    }
}

registry.category("actions").add("jto_home_menu.home", HomeMenu);
