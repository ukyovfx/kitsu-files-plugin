/** Monotonic generation token for async work that belongs to a selected Task. */
export function filesSurfaceMode(context = {}) {
  return context.driveSettings || !context.taskId ? 'admin' : 'task'
}

export function createRequestGeneration() {
  let generation = 0
  return {
    begin() { generation += 1; return generation },
    isCurrent(candidate) { return candidate === generation },
    invalidate() { generation += 1 }
  }
}
