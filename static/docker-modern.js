(function exposeRunvardDockerModern(global) {
  function groupKey(container) {
    const appGroup = container && container.app_group;
    if (appGroup && appGroup.id) return String(appGroup.id);
    return `container:${container?.name || container?.id || "unknown"}`;
  }

  function groupContainers(containers) {
    const groups = new Map();
    (containers || []).forEach((container) => {
      const key = groupKey(container);
      const appGroup = container?.app_group || {};
      if (!groups.has(key)) {
        const compose = appGroup.type === "compose";
        groups.set(key, {
          key,
          kind: compose ? "compose" : "container",
          name: compose
            ? appGroup.project || appGroup.name || container?.name || key
            : container?.name || appGroup.name || key,
          project: compose ? appGroup.project || "" : "",
          containers: [],
        });
      }
      groups.get(key).containers.push(container);
    });
    return Array.from(groups.values());
  }

  function groupState(containers) {
    const states = (containers || []).map((container) =>
      String(container?.state || "").toLowerCase(),
    );
    if (states.some((state) => state === "dead")) return "error";
    const running = states.filter((state) => state === "running").length;
    if (states.length && running === states.length) return "running";
    if (!running) return "stopped";
    return "partial";
  }

  function aggregateStats(containers, stats) {
    const totals = {
      cpu_percent: 0,
      mem_used: 0,
      mem_limit: 0,
      mem_percent: 0,
      available: 0,
    };
    (containers || []).forEach((container) => {
      const snapshot = stats && stats[container?.id];
      if (!snapshot || snapshot.error) return;
      totals.cpu_percent += Number(snapshot.cpu_percent) || 0;
      totals.mem_used += Number(snapshot.mem_used) || 0;
      totals.mem_limit += Number(snapshot.mem_limit) || 0;
      totals.available += 1;
    });
    totals.cpu_percent = Number(totals.cpu_percent.toFixed(1));
    totals.mem_percent = totals.mem_limit
      ? Number(((totals.mem_used / totals.mem_limit) * 100).toFixed(1))
      : 0;
    return totals;
  }

  global.RunvardDockerModern = {
    aggregateStats,
    groupContainers,
    groupState,
  };
})(typeof window === "undefined" ? globalThis : window);
