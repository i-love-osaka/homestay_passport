from django.contrib.admin import AdminSite


class CustomAdminSite(AdminSite):
    enable_nav_sidebar = True

    site_header = "护照收集系统后台"
    index_title = "护照收集系统后台"
    site_title = "护照收集系统后台"

    def get_app_list(self, request, app_label=None):
        app_list = super().get_app_list(request, app_label)

        # =====================================================
        # ① 顶层 App 分组顺序
        # =====================================================
        app_order = {
            "sync": 1,
            "passport": 2,
        }

        # =====================================================
        # ② 各分组内模型顺序
        # =====================================================
        passport_model_order = {
            "guest": 1,
            "house": 2,
            "lock": 3,
            "houselock": 4,
        }
        auth_model_order = {
            "user": 1,
            "group": 2,
            "logentry": 3,
        }

        # =====================================================
        # ③ 遍历处理
        # =====================================================
        for app in app_list:
            label = app["app_label"]

            # 修改显示名称
            if label == "passport":
                app["name"] = "民宿管理"
            elif label == "auth":
                app["name"] = "系统设置"

            # 分组内排序
            if label == "passport":
                app["models"].sort(
                    key=lambda m: passport_model_order.get(
                        m["object_name"].lower(), 100
                    )
                )
            elif label == "auth":
                app["models"].sort(
                    key=lambda m: auth_model_order.get(m["object_name"].lower(), 100)
                )

        # =====================================================
        # ④ 顶层排序
        # =====================================================
        app_list.sort(key=lambda app: app_order.get(app["app_label"], 100))

        return app_list
