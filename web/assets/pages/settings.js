import { el, modal, toast, iconButton, icon, emptyBox, loading, table, fmtTime } from '../ui.js';
import { canDo, ROLE_LABEL, store } from '../state.js';

export function createSettingsPage({api, shell, logout, go, checkUpdate}) {
function settingControl(item) {
  if (!item.editable) {
    return {
      node: el("div", { class: "row tight center" }, [
        el("span", {
          class: "mono" + (item.secret ? " dim" : ""),
          text: typeof item.value === "boolean" ? (item.value ? "开启" : "关闭") : String(item.value === "" ? "（空）" : item.value),
        }),
        el("span", { class: "tag tiny", text: "需重启" }),
      ]),
      get: () => undefined,
    };
  }

  const initial = item.raw;
  if (item.type === "bool") {
    const input = el("input", { type: "checkbox" });
    input.checked = !!initial;
    return {
      node: el("label", { class: "field-check inline" }, [input, el("span", { text: input.checked ? "开启" : "关闭" })]),
      get: () => input.checked,
      changed: () => input.checked !== !!initial,
    };
  }
  if (item.type === "choice") {
    const input = el(
      "select",
      { class: "input sm" },
      (item.choices || []).map((value) => el("option", { value: value, selected: value === initial }, value))
    );
    return { node: input, get: () => input.value, changed: () => input.value !== initial };
  }
  const input = el("input", {
    class: "input sm",
    type: item.type === "int" || item.type === "float" ? "number" : "text",
  });
  if (item.type === "int" || item.type === "float") {
    if (item.minimum !== null && item.minimum !== undefined) input.min = String(item.minimum);
    if (item.maximum !== null && item.maximum !== undefined) input.max = String(item.maximum);
    if (item.type === "float") input.step = "0.1";
  }
  input.value = initial === null || initial === undefined ? "" : String(initial);
  return {
    node: input,
    get: () => input.value.trim(),
    changed: () => input.value.trim() !== (initial === null || initial === undefined ? "" : String(initial)),
  };
}

function downloaderForm(schemaItems, existing) {
  const isEdit = Boolean(existing);
  // 编辑时锁定 provider：换 provider 等于换一套字段，语义上应该是删旧建新
  let provider = existing ? existing.provider : (schemaItems[0] || {}).provider;

  const open = () => {
    const spec = schemaItems.find((item) => item.provider === provider) || { fields: [] };
    const values = (existing && existing.values) || {};
    const fields = [
      {
        key: "name",
        label: "显示名",
        value: existing ? existing.name : spec.display_name,
        placeholder: "例：家里的 qBittorrent",
      },
      isEdit
        ? null
        : {
            key: "provider",
            label: "类型",
            type: "select",
            value: provider,
            options: schemaItems.map((item) => ({
              value: item.provider,
              label: item.display_name,
            })),
          },
    ].filter(Boolean);

    spec.fields.forEach((f) => {
      // 密码类字段已设置过就提示"留空不改"，避免用户以为丢了
      const isSecret = f.type === "password";
      const already = isSecret && values[f.key + "_set"];
      const raw = values[f.key];
      fields.push({
        key: f.key,
        label: f.label + (already ? "（已设置，留空不改）" : ""),
        type:
          f.type === "bool" ? "checkbox"
            : f.type === "password" ? "password"
            : f.type === "choice" ? "select"
            : f.type === "int" || f.type === "float" ? "number"
            : "text",
        value: isSecret
          ? ""
          : f.type === "bool"
          ? (raw === undefined ? Boolean(f.default) : Boolean(raw))
          : Array.isArray(raw)
          ? raw.join(",")
          : raw === undefined || raw === null
          ? (f.default === undefined ? "" : f.default)
          : raw,
        options: (f.choices || []).map((c) => ({ value: String(c), label: String(c) })),
        placeholder: f.placeholder || "",
        hint: f.hint || "",
      });
    });

    fields.push({
      key: "enabled",
      label: "启用",
      type: "checkbox",
      value: existing ? existing.enabled : false,
      hint: isEdit ? "" : "建议先保存再点「测试」验证连通性，通了再启用",
    });

    modal(
      isEdit ? "编辑下载器：" + existing.name : "添加下载器",
      fields,
      async (input) => {
        const picked = isEdit ? provider : input.provider || provider;
        const targetSpec =
          schemaItems.find((item) => item.provider === picked) || { fields: [] };
        const payloadValues = {};
        targetSpec.fields.forEach((f) => {
          const v = input[f.key];
          // 密码留空 = 不修改，别把空串提交上去把已存的密码清掉
          if (f.type === "password" && !String(v || "").trim()) return;
          if (v === undefined) return;
          payloadValues[f.key] = v;
        });
        const body = {
          name: input.name,
          provider: picked,
          enabled: Boolean(input.enabled),
          values: payloadValues,
        };
        if (isEdit) {
          await api("/downloaders/" + existing.id, { method: "PATCH", body: body });
        } else {
          await api("/downloaders", { method: "POST", body: body });
        }
        toast(isEdit ? "已保存" : "已添加，建议点「测试」验证连通性", "ok");
        pageSettings();
      },
      "保存",
      {
        lead: (schemaItems.find((item) => item.provider === provider) || {}).note || "",
      }
    );
  };
  open();
}

const SPEED_PHASE = {
  peak: { label: "限速时段内", cls: "warn" },
  off_peak: { label: "限速时段外", cls: "ok" },
  disabled: { label: "未启用", cls: "" },
};

function speedLimitCard(info, isAdmin) {
  const config = (info && info.config) || {};
  const current = (info && info.current) || {};
  const phase = SPEED_PHASE[current.phase] || SPEED_PHASE.disabled;
  // 0 在这里是「不限速」而不是「限到 0」——直接显示 0 会让人以为断流
  const kbText = (value) => (!value ? "不限速" : value + " KB/s");

  const body = el("div", {}, [
    el("div", { class: "kv" }, [
      el("div", { class: "kv-item" }, [
        el("div", { class: "kv-label", text: "当前状态" }),
        el("div", {}, [
          el("span", { class: "tag " + phase.cls + " tiny", text: phase.label }),
        ]),
      ]),
      el("div", { class: "kv-item" }, [
        el("div", { class: "kv-label", text: "此刻生效" }),
        el("div", { class: "tiny", text:
          "下行 " + kbText(current.download_kb) + " · 上行 " + kbText(current.upload_kb) }),
      ]),
      el("div", { class: "kv-item" }, [
        el("div", { class: "kv-label", text: "限速时段" }),
        el("div", { class: "mono tiny", text: (config.start || "-") + " ~ " + (config.end || "-") }),
      ]),
      el("div", { class: "kv-item" }, [
        el("div", { class: "kv-label", text: "下发周期" }),
        el("div", { class: "tiny", text: "每 " + (info.interval_minutes || 10) + " 分钟" }),
      ]),
    ]),
    el("div", { class: "divider" }),
    el("div", { class: "stack tiny" }, [
      el("div", { class: "dim", text:
        "时段内：下行 " + kbText(config.download_kb) + " · 上行 " + kbText(config.upload_kb) }),
      el("div", { class: "dim", text:
        "时段外：下行 " + kbText(config.off_peak_download_kb) +
        " · 上行 " + kbText(config.off_peak_upload_kb) }),
      el("div", { class: "dim", text: "时段可跨午夜，如 23:00 ~ 07:00 表示整个夜间" }),
    ]),
    isAdmin
      ? el("div", { class: "row tight", style: "margin-top:14px" }, [
          iconButton("配置时段", "edit", () => speedLimitForm(config), "sm primary"),
          iconButton("立即下发", "play", async () => {
            try {
              const result = await api("/downloaders/speed-limit/apply", { method: "POST" });
              toast(result.message || "已下发", "ok");
              pageSettings();
            } catch (error) {
              toast(error.message, "err");
            }
          }, "sm"),
        ])
      : null,
  ]);

  return el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", {}, [icon("clock", "sm"), el("span", { text: "下载器限速时段" })]),
      config.enabled
        ? el("span", { class: "tag dot ok", text: "已启用" })
        : el("span", { class: "tag dot", text: "已关闭" }),
    ]),
    body,
  ]);
}

function speedLimitForm(config) {
  modal(
    "配置下载器限速时段",
    [
      { key: "enabled", label: "启用限速时段", type: "checkbox", value: !!config.enabled },
      { key: "start", label: "限速开始时间", value: config.start || "08:00", placeholder: "08:00" },
      {
        key: "end",
        label: "限速结束时间",
        value: config.end || "23:00",
        placeholder: "23:00",
        hint: "可跨午夜：填 23:00 ~ 07:00 表示夜间限速、白天放开",
      },
      { key: "download_kb", label: "时段内下行限速（KB/s，0=不限）", type: "number", value: config.download_kb || 0 },
      { key: "upload_kb", label: "时段内上行限速（KB/s，0=不限）", type: "number", value: config.upload_kb || 0 },
      {
        key: "off_peak_download_kb",
        label: "时段外下行限速（KB/s，0=不限）",
        type: "number",
        value: config.off_peak_download_kb || 0,
        hint: "通常留 0：夜间跑满",
      },
      { key: "off_peak_upload_kb", label: "时段外上行限速（KB/s，0=不限）", type: "number", value: config.off_peak_upload_kb || 0 },
    ],
    async (values) => {
      await api("/downloaders/speed-limit", {
        method: "PUT",
        body: {
          enabled: !!values.enabled,
          start: values.start,
          end: values.end,
          download_kb: Number(values.download_kb) || 0,
          upload_kb: Number(values.upload_kb) || 0,
          off_peak_download_kb: Number(values.off_peak_download_kb) || 0,
          off_peak_upload_kb: Number(values.off_peak_upload_kb) || 0,
        },
      });
      toast("已保存，下一轮会自动下发（也可点「立即下发」）", "ok");
      pageSettings();
    },
    "保存",
    {
      wide: true,
      lead: "单位统一是 KB/s——各下载器内部单位不同（qB 用 B/s、TR 用 KB/s），换算由服务端完成。",
    }
  );
}

function downloaderCard(list, schemaItems, isAdmin) {
  const rows = list || [];
  const body = rows.length
    ? el("div", { class: "list" }, rows.map((row) => {
        const spec = schemaItems.find((item) => item.provider === row.provider);
        const okState = String(row.last_status || "").indexOf("正常") === 0;
        return el("div", { class: "list-row" }, [
          el("span", {}, [
            el("div", { class: "row tight center", style: "gap:6px" }, [
              el("span", { text: row.name }),
              el("span", {
                class: "tag dot tiny " + (row.enabled ? "ok" : ""),
                text: row.enabled ? "已启用" : "已禁用",
              }),
            ]),
            el("div", { class: "cell-sub tiny" }, [
              (spec && spec.display_name) || row.provider,
              row.values && row.values.url ? " · " + row.values.url : "",
              " · 优先级 " + row.priority,
            ]),
            row.last_status
              ? el("div", { class: "tiny dim" }, [
                  okState
                    ? el("span", { text: row.last_status })
                    : el("span", { class: "tag warn tiny", text: row.last_status }),
                ])
              : el("div", { class: "tiny dim", text: "未检测" }),
          ]),
          el("span", {}, [
            isAdmin
              ? el("div", { class: "row tight" }, [
                  (() => {
                    const test = el("button", { class: "btn sm" }, [
                      icon("check", "sm"),
                      el("span", { text: "测试" }),
                    ]);
                    test.addEventListener("click", async () => {
                      test.disabled = true;
                      test.querySelector("span").textContent = "检测中…";
                      try {
                        const r = await api("/downloaders/" + row.id + "/test", { method: "POST" });
                        toast(row.name + "：" + r.message, r.success ? "ok" : "err");
                        pageSettings();
                      } catch (error) {
                        toast(error.message, "err");
                        test.disabled = false;
                        test.querySelector("span").textContent = "测试";
                      }
                    });
                    return test;
                  })(),
                  iconButton(row.enabled ? "禁用" : "启用", row.enabled ? "pause" : "play",
                    async () => {
                      try {
                        await api("/downloaders/" + row.id, {
                          method: "PATCH",
                          body: {
                            name: row.name,
                            provider: row.provider,
                            enabled: !row.enabled,
                            values: {},
                          },
                        });
                        pageSettings();
                      } catch (error) {
                        toast(error.message, "err");
                      }
                    }, "sm"),
                  iconButton("编辑", "edit",
                    () => downloaderForm(schemaItems, row), "sm ghost"),
                  iconButton("删除", "trash", async () => {
                    if (!confirm("确定删除下载器 " + row.name + "？")) return;
                    try {
                      await api("/downloaders/" + row.id, { method: "DELETE" });
                      toast("已删除", "ok");
                      pageSettings();
                    } catch (error) {
                      toast(error.message, "err");
                    }
                  }, "sm danger"),
                ])
              : el("span", { class: "tiny dim", text: row.enabled ? "已启用" : "已禁用" }),
          ]),
        ]);
      }))
    : el("div", { class: "pad-sm" }, [
        emptyBox("还没有配置下载器。BT 资源需要 qBittorrent/Transmission，" +
                 "榜单里的 B 站/YouTube 视频需要 yt-dlp。", "download"),
      ]);

  return el("div", { class: "card flush" }, [
    el("div", { class: "card-head" }, [
      el("h3", {}, [icon("download", "sm"), el("span", { text: "下载器" })]),
      el("div", { class: "row tight center" }, [
        el("span", { class: "tag", text: "已启用 " + rows.filter((r) => r.enabled).length + "/" + rows.length }),
        isAdmin
          ? iconButton("添加", "plus", () => downloaderForm(schemaItems, null), "sm primary")
          : null,
      ]),
    ]),
    body,
  ]);
}

async function pageSettings() {
  shell(loading(), "设置", "在线修改配置并立即生效");
  // 下载器数据拉不到不该让整个设置页白屏（比如老版本后端没这个接口），
  // 所以两个下载器请求都做降级：失败就当没有下载器，其余设置照常显示。
  const [data, info, me, dlList, dlSchema, speedInfo] = await Promise.all([
    api("/system/settings"),
    api("/system/info"),
    api("/auth/me").catch(() => null),
    api("/downloaders").catch(() => ({ items: [] })),
    api("/downloaders/schema").catch(() => ({ items: [] })),
    // 限速时段同样降级：老后端没这个接口时当作"未配置"，不让设置页整页失败
    api("/downloaders/speed-limit").catch(() => ({ data: null })),
  ]);
  const isAdmin = canDo("admin");
  const controls = {};

  // 把「一项都改不了」的组单独挑出来：它们（服务/目录/安全）只能改
  // .env 或 config.yaml 后重启，摆在页面上只是拉长滚动条（本轮需求 3）。
  // 不是直接删掉——用户仍需要查当前生效值，所以收进一个可展开的卡片。
  const allGroups = data.groups || [];
  const editableGroups = allGroups.filter((group) =>
    group.items.some((item) => item.editable)
  );
  const readonlyGroups = allGroups.filter(
    (group) => !group.items.some((item) => item.editable)
  );

  const groups = editableGroups.map((group) =>
    el("div", { class: "card flush" }, [
      el("div", { class: "card-head" }, [
        el("h3", {}, [icon("settings", "sm"), el("span", { text: group.title })]),
        el("span", {
          class: "tag tiny",
          text: group.items.filter((item) => item.editable).length + " 项可改",
        }),
      ]),
      table(
        [
          {
            title: "配置项",
            render: (row) =>
              el("div", {}, [
                el("div", { text: row.label || row.key }),
                el("div", { class: "cell-sub mono tiny", text: row.env }),
              ]),
          },
          {
            title: "值",
            render: (row) => {
              // 非管理员一律只读展示：即使前端放开了，服务端也会 403
              if (row.managed_by === "schedules") {
                return el("div", { class: "stack tiny" }, [
                  el("span", { text: String(row.value ?? "-") }),
                  iconButton("前往定时任务", "clock", () => go("schedules")),
                ]);
              }
              const control = isAdmin ? settingControl(row) : settingControl({ ...row, editable: false });
              if (row.editable && isAdmin) controls[row.key] = control;
              return control.node;
            },
          },
          {
            title: "说明",
            render: (row) =>
              el("div", { class: "stack tiny" }, [
                row.hint ? el("div", { class: "dim", text: row.hint }) : null,
                row.reschedule ? el("div", { class: "dim", text: "改动后会重建定时任务触发器" }) : null,
                row.overridden ? el("span", { class: "tag brand tiny", text: "已被在线修改" }) : null,
              ]),
          },
        ],
        group.items,
        "无"
      ),
    ])
  );

  const saveBar = isAdmin
    ? el("div", { class: "card" }, [
        el("div", { class: "card-head" }, [
          el("h3", {}, [icon("check", "sm"), el("span", { text: "保存改动" })]),
          el("span", { class: "tag tiny", text: data.editable_total + " 项可在线修改" }),
        ]),
        el("div", { class: "muted", text: data.note }),
        el("div", { class: "row tight", style: "margin-top:14px" }, [
          iconButton("保存并生效", "check", async () => {
            const values = {};
            Object.keys(controls).forEach((key) => {
              const control = controls[key];
              // 只提交真正改过的项：整份提交会把「界面显示的旧值」当成用户意图写回去
              if (!control.changed || control.changed()) values[key] = control.get();
            });
            if (!Object.keys(values).length) {
              toast("没有改动");
              return;
            }
            try {
              const result = await api("/system/settings", { method: "PUT", body: { values: values } });
              toast(result.message, "ok");
              pageSettings();
            } catch (error) {
              toast(error.message, "err");
            }
          }, "primary"),
          (data.overridden || []).length
            ? iconButton("全部恢复默认", "refresh", async () => {
                if (!confirm("把 " + data.overridden.length + " 项在线修改全部恢复为配置文件里的值？")) return;
                try {
                  const result = await api("/system/settings/reset", { method: "POST", body: { keys: null } });
                  toast(result.message, "ok");
                  pageSettings();
                } catch (error) {
                  toast(error.message, "err");
                }
              }, "ghost")
            : null,
        ]),
        (data.overridden || []).length
          ? el("div", { class: "row tight wrap", style: "margin-top:12px" },
              data.overridden.map((key) => el("span", { class: "tag brand tiny", text: key })))
          : null,
      ])
    : el("div", { class: "card" }, [
        el("div", { class: "card-head" }, [el("h3", {}, [icon("info", "sm"), el("span", { text: "只读模式" })])]),
        el("div", { class: "muted", text: "当前角色（" + (ROLE_LABEL[store.role] || store.role) + "）不能修改配置，请联系管理员。" }),
      ]);

  /** 只读配置卡片：默认收起，点开才渲染表格。

      这些项（HOST/PORT/密钥/目录…）改了必须重启进程才生效，界面给输入框
      就是假功能（ADR-18），所以只提供**查看**。默认收起是因为日常配置
      用不到它们，摊开只会让页面变长（本轮需求 3）。
  */
  const readonlyCard = readonlyGroups.length
    ? (() => {
        const bodyBox = el("div", {});
        let open = false;
        const toggle = el("button", { class: "btn sm ghost" }, [
          icon("chevron-down", "sm"),
          el("span", { text: "展开查看" }),
        ]);
        const total = readonlyGroups.reduce((sum, g) => sum + g.items.length, 0);
        const render = () => {
          if (!open) {
            bodyBox.replaceChildren();
            toggle.querySelector("span").textContent = "展开查看";
            return;
          }
          toggle.querySelector("span").textContent = "收起";
          bodyBox.replaceChildren(
            ...readonlyGroups.map((group) =>
              el("div", { class: "card soft" }, [
                el("div", { class: "card-head" }, [
                  el("h3", {}, [el("span", { text: group.title })]),
                ]),
                el("div", { class: "list" }, group.items.map((row) =>
                  el("div", { class: "list-row" }, [
                    el("span", {}, [
                      el("div", { text: row.label || row.key }),
                      el("div", { class: "cell-sub mono tiny", text: row.env }),
                    ]),
                    el("span", { class: "mono tiny", text:
                      typeof row.value === "boolean"
                        ? (row.value ? "开启" : "关闭")
                        : String(row.value === "" ? "（空）" : row.value) }),
                  ])
                )),
              ])
            )
          );
        };
        toggle.addEventListener("click", () => {
          open = !open;
          render();
        });
        return el("div", { class: "card" }, [
          el("div", { class: "card-head" }, [
            el("h3", {}, [icon("info", "sm"), el("span", { text: "只读配置（需重启生效）" })]),
            el("div", { class: "row tight center" }, [
              el("span", { class: "tag tiny", text: total + " 项" }),
              toggle,
            ]),
          ]),
          el("div", { class: "muted tiny", text:
            readonlyGroups.map((g) => g.title).join("、") +
            " —— 这些项只能改 .env 或 config/config.yaml 后重启容器，界面不提供修改入口。" }),
          bodyBox,
        ]);
      })()
    : null;

  const accountCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", {}, [icon("users", "sm"), el("span", { text: "账号" })]),
      el("span", { class: "tag brand", text: (me && me.role_label) || ROLE_LABEL[store.role] || "-" }),
    ]),
    el("div", { class: "kv" }, [
      el("div", { class: "kv-item" }, [
        el("div", { class: "kv-label", text: "用户名" }),
        el("div", { text: (me && me.username) || store.username }),
      ]),
      el("div", { class: "kv-item" }, [
        el("div", { class: "kv-label", text: "上次登录" }),
        el("div", { class: "tiny", text: fmtTime(me && me.last_login_at) }),
      ]),
      el("div", { class: "kv-item" }, [
        el("div", { class: "kv-label", text: "版本" }),
        el("div", { class: "mono", text: "v" + info.version }),
      ]),
      el("div", { class: "kv-item" }, [
        el("div", { class: "kv-label", text: "调度器" }),
        el("div", { text: info.scheduler_running ? "运行中" : "已停止" }),
      ]),
    ]),
    el("div", { class: "row tight", style: "margin-top:16px" }, [
      iconButton("修改密码", "edit", () => {
        modal(
          "修改密码",
          [
            { key: "old_password", label: "原密码", type: "password" },
            { key: "new_password", label: "新密码", type: "password", hint: "至少 6 位" },
          ],
          async (values) => {
            await api(
              "/auth/password?old_password=" +
                encodeURIComponent(values.old_password) +
                "&new_password=" +
                encodeURIComponent(values.new_password),
              { method: "POST" }
            );
            toast("密码已更新，请重新登录", "ok");
            setTimeout(() => logout(true), 800);
          },
          "更新"
        );
      }, "sm primary"),
      isAdmin
        ? iconButton("测试通知渠道", "info", async () => {
            try {
              const result = await api("/system/notify/test", { method: "POST" });
              toast(result.message, result.success ? "ok" : "err");
            } catch (error) {
              toast(error.message, "err");
            }
          }, "sm")
        : null,
      isAdmin ? iconButton("用户管理", "users", () => go("users"), "sm ghost") : null,
    ]),
  ]);

  const noteCard = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", {}, [icon("info", "sm"), el("span", { text: "配置优先级" })]),
    ]),
    el("div", { class: "stack" }, [
      el("div", { class: "muted", text: "1. 界面在线修改（存数据库，最高优先级，重启仍在）" }),
      el("div", { class: "muted", text: "2. 环境变量 CF_xxx / .env" }),
      el("div", { class: "muted", text: "3. config/config.yaml" }),
    ]),
    el("div", { class: "divider" }),
    el("div", { class: "kv" }, [
      el("div", { class: "kv-item" }, [
        el("div", { class: "kv-label", text: "配置文件" }),
        el("div", { class: "mono tiny", text: data.config_file }),
      ]),
      el("div", { class: "kv-item" }, [
        el("div", { class: "kv-label", text: "文件状态" }),
        el("div", { text: data.config_file_exists ? "已存在" : "不存在（走默认值/.env）" }),
      ]),
    ]),
  ]);

  shell(
    el("div", { class: "grid" }, [
      el("div", { class: "grid cols-2" }, [accountCard, noteCard]),
      // 下载器放在保存栏之前：它是独立的增删改，不受「保存并生效」影响，
      // 摆在最上面也符合"配置下载器"是新装机第一件事的使用顺序。
      downloaderCard(dlList.items || [], dlSchema.items || [], isAdmin),
      // 限速时段紧跟下载器：它配的就是下载器的行为，挨在一起才好理解
      speedInfo && speedInfo.data ? speedLimitCard(speedInfo.data, isAdmin) : null,
      saveBar,
      // 可改的配置组用多列瀑布流：原先单列纵向排 13 张卡片，页面极长
      // 需要一直滚（本轮需求 3）。cols-settings 在宽屏给 2~3 列。
      el("div", { class: "grid cols-settings" }, groups),
      readonlyCard,
    ].filter(Boolean)),
    "设置",
    editableGroups.length + " 组可改 · " + data.editable_total +
      " 项可在线改 · " + (dlList.items || []).length + " 个下载器 · 敏感项已脱敏",
    [
      iconButton("检查更新", "download", () => checkUpdate(false)),
      iconButton("刷新", "refresh", () => pageSettings()),
    ]
  );
}
return pageSettings;
}
