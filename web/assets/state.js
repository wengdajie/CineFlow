const THEME_KEY = "cf_theme";

const store = {
  token: localStorage.getItem("cf_token") || "",
  username: localStorage.getItem("cf_user") || "",
  // 角色只用于**隐藏没权限的入口**（少点误操作），真正的鉴权在服务端
  role: localStorage.getItem("cf_role") || "admin",
  page: location.hash.replace("#", "") || "dashboard",
  theme: localStorage.getItem(THEME_KEY) || "auto",
};

const ROLE_RANK = { viewer: 1, operator: 2, admin: 3 };

const ROLE_LABEL = { viewer: "访客", operator: "操作员", admin: "管理员" };

const canDo = (minimum) => (ROLE_RANK[store.role] || 3) >= (ROLE_RANK[minimum] || 3);

export { THEME_KEY, store, ROLE_LABEL, canDo };
