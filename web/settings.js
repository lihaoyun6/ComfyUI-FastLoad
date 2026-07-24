import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

app.registerExtension({
    name: "FastLoad.Settings",
    async init() {
        app.ui.settings.addSetting({
            id: "FastLoad.EnabledFastLoad",
            category: ["FastLoad", "General", "Enabled FastLoad"],
            name: "Enabled FastLoad",
            type: "boolean",
            defaultValue: true,
            onChange: async (value) => {
                try {
                    const response = await api.fetchApi("/fastload/config", {
                        method: "POST",
                        body: JSON.stringify({ enabled: value }),
                    });
                } catch (error) {
                    console.error("toggling error", error);
                }
            }
        });
        
        app.ui.settings.addSetting({
            id: "FastLoad.CacheExtensions",
            category: ["FastLoad", "More Options", "Cache Extensions"],
            name: "Cache Extensions",
            type: "boolean",
            defaultValue: true,
            tooltip: "Cache Extension Scripts (Available only when Aggressive Caching is enabled)",
            onChange: async (value) => {
                try {
                    const response = await api.fetchApi("/fastload/config", {
                        method: "POST",
                        body: JSON.stringify({ ext_cache: value }),
                    });
                } catch (error) {
                    console.error("toggling error", error);
                }
            }
        });
        
        app.ui.settings.addSetting({
            id: "FastLoad.AggressiveCaching",
            category: ["FastLoad", "More Options", "Aggressive Caching"],
            name: "Aggressive Caching",
            type: "boolean",
            defaultValue: true,
            tooltip: "Maximize ComfyUI loading speed (experimental)",
            onChange: async (value) => {
                try {
                    const response = await api.fetchApi("/fastload/config", {
                        method: "POST",
                        body: JSON.stringify({ max_cache: value }),
                    });
                } catch (error) {
                    console.error("toggling error", error);
                }
            }
        });
    }
});