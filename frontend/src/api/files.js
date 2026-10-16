const ROOT = 'plugins/vfx_files'
const REVIEW_POLICY_ID = 'files-page-review-v1'
const REVIEW_POLICY = 'supervisor approval requires a completed qc-passed publish and a reviewer distinct from its publisher'
import { sha256 } from '@noble/hashes/sha2.js'
import { bytesToHex } from '@noble/hashes/utils.js'

async function readJson(response) {
  if (!response.ok) {
    const error = new Error(response.status === 503 ? 'Files is temporarily unavailable. Retry when the service is ready.' : `Files request failed (${response.status}).`)
    error.status = response.status
    throw error
  }
  return response.json()
}

export function createFilesApi(client) {
  async function csrfToken() {
    return readJson(await client.request('GET', `${ROOT}/csrf-token`)).then((data) => data.csrf_token)
  }

  async function mutation(method, path, options = {}) {
    return client.request(method, path, { ...options, csrfToken: await csrfToken() })
  }

  return {
    async listProduction(productionId, episodeId, taskId) {
      if (!productionId) throw new TypeError('A Production ID is required')
      const query = new URLSearchParams({ production_id: productionId })
      if (episodeId) query.set('episode_id', episodeId)
      if (taskId) query.set('task_id', taskId)
      const response = await client.request('GET', `${ROOT}/overview?${query}`)
      return readJson(response)
    },

    async getTaskSharedFolders(productionId, taskId) {
      if (!productionId || !taskId) throw new TypeError('A Production and Task are required')
      return readJson(await client.request('GET', `${ROOT}/productions/${encodeURIComponent(productionId)}/tasks/${encodeURIComponent(taskId)}/shared-folders`))
    },

    async getDriveAuthorityStatus(productionId) {
      if (!productionId) throw new TypeError('A Production ID is required')
      return readJson(await client.request('GET', `${ROOT}/productions/${encodeURIComponent(productionId)}/drive/status`))
    },

    async listDriveConnections(productionId) {
      if (!productionId) throw new TypeError('A Production ID is required')
      return readJson(await client.request('GET', `${ROOT}/productions/${encodeURIComponent(productionId)}/drive/connections`))
    },

    async listDriveRootCandidates(productionId, connectionId) {
      if (!productionId || !connectionId) throw new TypeError('A Production and Drive connection are required')
      return readJson(await client.request('GET', `${ROOT}/productions/${encodeURIComponent(productionId)}/drive/connections/${encodeURIComponent(connectionId)}/roots`))
    },

    async createDriveAuthorization(productionId, accountMode, idempotencyKey) {
      if (!productionId || accountMode !== 'my_drive' || !idempotencyKey) {
        throw new TypeError('A Production, Drive account mode and idempotency key are required')
      }
      return readJson(await mutation('POST', `${ROOT}/productions/${encodeURIComponent(productionId)}/drive/authorizations`, {
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
        body: JSON.stringify({ contract_version: '1.0', account_mode: accountMode })
      }))
    },

    async selectDriveRoot(productionId, connectionId, rootId, configRevision, idempotencyKey) {
      if (!productionId || !connectionId || !rootId || !Number.isInteger(configRevision) || configRevision < 0 || !idempotencyKey) {
        throw new TypeError('A Production, connection, root, revision and idempotency key are required')
      }
      return readJson(await mutation('POST', `${ROOT}/productions/${encodeURIComponent(productionId)}/drive/connections/${encodeURIComponent(connectionId)}/roots`, {
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': idempotencyKey,
          'If-Match': `"${configRevision}"`
        },
        body: JSON.stringify({ contract_version: '1.0', root_id: rootId })
      }))
    },

    async listTaskUploads(taskId) {
      return readJson(await client.request('GET', `${ROOT}/tasks/${encodeURIComponent(taskId)}/uploads`))
    },

    async listTaskWorkingRevisions(taskId) {
      if (!taskId) throw new TypeError('A Task ID is required')
      return readJson(await client.request('GET', `${ROOT}/tasks/${encodeURIComponent(taskId)}/working-revisions`))
    },

    async getTaskPublishOptions(taskId) {
      if (!taskId) throw new TypeError('A Task ID is required')
      return readJson(await client.request('GET', `${ROOT}/tasks/${encodeURIComponent(taskId)}/publish-options`))
    },

    async getTaskOutputPackageOptions(taskId) {
      if (!taskId) throw new TypeError('A Task ID is required')
      return readJson(await client.request('GET', `${ROOT}/tasks/${encodeURIComponent(taskId)}/output-package-options`))
    },

    async listTaskStorageObjects(taskId, parentId, pageToken) {
      if (!taskId) throw new TypeError('A Task ID is required')
      const query = new URLSearchParams()
      if (parentId) query.set('parent_id', parentId)
      if (pageToken) query.set('page_token', pageToken)
      return readJson(await client.request('GET', `${ROOT}/tasks/${encodeURIComponent(taskId)}/storage-objects?${query}`))
    },

    async listTaskOutputPackages(taskId, offset = 0) {
      if (!taskId) throw new TypeError('A Task ID is required')
      const query = new URLSearchParams({ limit: '50', offset: String(offset) })
      return readJson(await client.request('GET', `${ROOT}/tasks/${encodeURIComponent(taskId)}/output-packages?${query}`))
    },

    async createTaskOutputPackage(taskId, descriptor, idempotencyKey) {
      if (!taskId || !descriptor?.output_type_id || !descriptor?.package_type || !descriptor?.files?.length) {
        throw new TypeError('A Task, Output Type, package type and at least one file are required')
      }
      if (!idempotencyKey) throw new TypeError('An idempotency key is required')
      return readJson(await mutation('POST', `${ROOT}/tasks/${encodeURIComponent(taskId)}/output-packages`, {
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
        body: JSON.stringify(descriptor)
      }))
    },

    async listOutputPackageFiles(packageId, offset = 0) {
      if (!packageId) throw new TypeError('An Output Package ID is required')
      const query = new URLSearchParams({ limit: '200', offset: String(offset) })
      return readJson(await client.request('GET', `${ROOT}/output-packages/${encodeURIComponent(packageId)}/files?${query}`))
    },

    outputPackageDownloadUrl(fileId) {
      return `/api/${ROOT}/output-package-files/${encodeURIComponent(fileId)}/download`
    },

    async listOutputFamilies(productionId) {
      if (!productionId) throw new TypeError('A Production ID is required')
      return readJson(await client.request('GET', `${ROOT}/productions/${encodeURIComponent(productionId)}/output-families`))
    },

    async createPublish(taskId, outputFamilyId, sourceRevisionFileId) {
      if (!taskId || !outputFamilyId || !sourceRevisionFileId) throw new TypeError('A Task, output family and completed Working File are required')
      return readJson(await mutation('POST', `${ROOT}/tasks/${encodeURIComponent(taskId)}/publishes`, {
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': `publish:${crypto.randomUUID()}` },
        body: JSON.stringify({ output_family_id: outputFamilyId, source_revision_file_id: sourceRevisionFileId })
      }))
    },

    async approvePublish(publishId) {
      if (!publishId) throw new TypeError('A Publish ID is required')
      return readJson(await mutation('POST', `${ROOT}/publishes/${encodeURIComponent(publishId)}/approvals`, {
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': `approval:${crypto.randomUUID()}` },
        body: JSON.stringify({ policy_id: REVIEW_POLICY_ID, policy_sha256: bytesToHex(sha256(new TextEncoder().encode(REVIEW_POLICY))) })
      }))
    },

    async createHandoff(publishId, recipientId) {
      if (!publishId || !recipientId) throw new TypeError('A Publish and recipient are required')
      return readJson(await mutation('POST', `${ROOT}/publishes/${encodeURIComponent(publishId)}/handoffs`, {
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': `handoff:${crypto.randomUUID()}` },
        body: JSON.stringify({ recipient_id: recipientId })
      }))
    },

    async createDepartmentHandoff(publishId, recipientDepartmentId, recipientTaskId, recipientPersonId = null) {
      if (!publishId || !recipientDepartmentId || !recipientTaskId) throw new TypeError('A Publish, receiving Department and receiving Task are required')
      const body = { recipient_department_id: recipientDepartmentId, recipient_task_id: recipientTaskId }
      if (recipientPersonId) body.recipient_person_id = recipientPersonId
      return readJson(await mutation('POST', `${ROOT}/publishes/${encodeURIComponent(publishId)}/handoffs`, {
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': `handoff:${crypto.randomUUID()}` },
        body: JSON.stringify(body)
      }))
    },

    workingFileDownloadUrl(fileId) {
      return `/api/${ROOT}/working-files/${encodeURIComponent(fileId)}/download`
    },

    async stageWorkingSnapshot(taskId, files, baseRevisionId = null, onProgress = () => {}, fileType = 'working', includeIntermediate = false) {
      if (!taskId || !files?.length) throw new TypeError('A Task and at least one file are required')
      if (typeof fileType !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9 _-]{0,31}$/.test(fileType)) {
        throw new TypeError('File type must be 1 to 32 letters, digits, spaces, underscores or hyphens')
      }
      if (typeof includeIntermediate !== 'boolean') throw new TypeError('Intermediate file opt-in must be explicit')
      const descriptors = []
      for (const file of files) {
        const digest = sha256.create()
        const blockSize = 8 * 1024 * 1024
        for (let offset = 0; offset < file.size; offset += blockSize) {
          digest.update(new Uint8Array(await file.slice(offset, Math.min(offset + blockSize, file.size)).arrayBuffer()))
        }
        descriptors.push({ name: file.name, size: file.size, sha256: bytesToHex(digest.digest()), category: fileType })
      }
      const revision = await readJson(await mutation('POST', `${ROOT}/tasks/${encodeURIComponent(taskId)}/working-revisions`, {
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': `working:${crypto.randomUUID()}` },
        body: JSON.stringify({ base_revision_id: baseRevisionId, files: descriptors, dependencies: [], include_intermediate: includeIntermediate })
      }))
      const jobs = []
      let completedBytes = 0
      const totalBytes = files.reduce((sum, file) => sum + file.size, 0)
      for (const [index, file] of files.entries()) {
        const sessionId = revision.files[index]?.upload_session_id
        if (!sessionId) throw new Error('The Working revision has no resumable upload session for a file.')
        const status = await readJson(await client.request('GET', `${ROOT}/uploads/${encodeURIComponent(sessionId)}`))
        const chunkSize = status.max_chunk_bytes || 8 * 1024 * 1024
        const stagedParts = new Set(status.parts.filter((part) => part.state === 'staged').map((part) => part.index))
        for (let partIndex = 0; partIndex < Math.ceil(file.size / chunkSize); partIndex += 1) {
          const start = partIndex * chunkSize
          const end = Math.min(start + chunkSize, file.size)
          const bytes = new Uint8Array(await file.slice(start, end).arrayBuffer())
          if (!stagedParts.has(partIndex)) {
            await readJson(await mutation('PUT', `${ROOT}/uploads/${encodeURIComponent(sessionId)}/parts/${partIndex}`, {
              headers: {
                'Content-Type': 'application/octet-stream',
                'Content-Range': `bytes ${start}-${end - 1}/${file.size}`,
                'X-Chunk-SHA256': bytesToHex(sha256(bytes))
              },
              body: bytes.buffer
            }))
          }
          completedBytes += end - start
          onProgress({ completed: Math.min(completedBytes, totalBytes), total: totalBytes, file: file.name })
        }
        jobs.push(await readJson(await mutation('POST', `${ROOT}/uploads/${encodeURIComponent(sessionId)}/finalize`, {
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        })))
      }
      return { revision, jobs }
    },

    async resumeWorkingSnapshot(taskId, revision, files, onProgress = () => {}) {
      if (!taskId || !revision?.id || files?.length !== revision.files?.length) {
        throw new TypeError('Select every file from the incomplete Working snapshot to resume it.')
      }
      const actual = []
      for (const file of files) {
        const digest = sha256.create()
        for (let offset = 0; offset < file.size; offset += 8 * 1024 * 1024) {
          digest.update(new Uint8Array(await file.slice(offset, Math.min(offset + 8 * 1024 * 1024, file.size)).arrayBuffer()))
        }
        actual.push({ name: file.name, size: file.size, sha256: bytesToHex(digest.digest()) })
      }
      const expected = revision.files.map(({ name, size, sha256 }) => ({ name, size, sha256 }))
      if (actual.some((file, index) => file.name !== expected[index].name || file.size !== expected[index].size || file.sha256 !== expected[index].sha256)) {
        throw new Error('Selected files do not match the reserved Working snapshot; no upload was resumed.')
      }
      const jobs = []
      let completedBytes = 0
      const totalBytes = files.reduce((sum, file) => sum + file.size, 0)
      for (const [index, file] of files.entries()) {
        const sessionId = revision.files[index].upload_session_id
        const status = await readJson(await client.request('GET', `${ROOT}/uploads/${encodeURIComponent(sessionId)}`))
        const chunkSize = status.max_chunk_bytes || 8 * 1024 * 1024
        const stagedParts = new Map(status.parts.filter((part) => part.state === 'staged').map((part) => [part.index, part.byte_count]))
        for (let partIndex = 0; partIndex < Math.ceil(file.size / chunkSize); partIndex += 1) {
          const start = partIndex * chunkSize
          const end = Math.min(start + chunkSize, file.size)
          const bytes = new Uint8Array(await file.slice(start, end).arrayBuffer())
          if (!stagedParts.has(partIndex)) {
            await readJson(await mutation('PUT', `${ROOT}/uploads/${encodeURIComponent(sessionId)}/parts/${partIndex}`, {
              headers: {
                'Content-Type': 'application/octet-stream',
                'Content-Range': `bytes ${start}-${end - 1}/${file.size}`,
                'X-Chunk-SHA256': bytesToHex(sha256(bytes))
              },
              body: bytes.buffer
            }))
          }
          completedBytes += stagedParts.get(partIndex) || end - start
          onProgress({ completed: Math.min(completedBytes, totalBytes), total: totalBytes, file: file.name })
        }
        jobs.push(await readJson(await mutation('POST', `${ROOT}/uploads/${encodeURIComponent(sessionId)}/finalize`, {
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        })))
      }
      return { revision, jobs }
    },

    async stageFile(taskId, file, onProgress = () => {}) {
      const preview = 8 * 1024 * 1024
      const digest = sha256.create()
      for (let offset = 0; offset < file.size; offset += preview) {
        digest.update(new Uint8Array(await file.slice(offset, Math.min(offset + preview, file.size)).arrayBuffer()))
      }
      const fileHash = bytesToHex(digest.digest())
      const sessions = await this.listTaskUploads(taskId)
      let session = sessions.find((item) => item.expected_size === file.size && item.expected_sha256 === fileHash && item.state === 'receiving')
      if (!session) {
        session = await readJson(await mutation('POST', `${ROOT}/tasks/${encodeURIComponent(taskId)}/uploads`, {
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ size: file.size, sha256: fileHash, file_name: file.name })
        }))
      }
      let status = await readJson(await client.request('GET', `${ROOT}/uploads/${encodeURIComponent(session.id)}`))
      const chunkSize = status.max_chunk_bytes || session.max_chunk_bytes || 8 * 1024 * 1024
      const completeParts = new Set(status.parts.filter((part) => part.state === 'staged').map((part) => part.index))
      let progressBytes = status.parts.filter((part) => part.state === 'staged').reduce((sum, part) => sum + part.byte_count, 0)
      onProgress({ completed: progressBytes, total: file.size })
      const chunkCount = Math.ceil(file.size / chunkSize)
      for (let index = 0; index < chunkCount; index += 1) {
        const start = index * chunkSize
        const end = Math.min(start + chunkSize, file.size)
        const bytes = new Uint8Array(await file.slice(start, end).arrayBuffer())
        if (!completeParts.has(index)) {
          const chunkHash = bytesToHex(sha256(bytes))
          const response = await mutation('PUT', `${ROOT}/uploads/${encodeURIComponent(session.id)}/parts/${index}`, {
            headers: {
              'Content-Type': 'application/octet-stream',
              'Content-Range': `bytes ${start}-${end - 1}/${file.size}`,
              'X-Chunk-SHA256': chunkHash
            },
            body: bytes.buffer
          })
          await readJson(response)
          progressBytes += end - start
        }
        onProgress({ completed: Math.min(progressBytes, file.size), total: file.size })
      }
      status = await readJson(await client.request('GET', `${ROOT}/uploads/${encodeURIComponent(session.id)}`))
      const result = await readJson(await mutation('POST', `${ROOT}/uploads/${encodeURIComponent(session.id)}/finalize`, {
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      }))
      return { ...result, sessionId: session.id, sha256: fileHash }
    },

    async respondHandoff(handoffId, target, feedback = '') {
      const response = await mutation('POST', `${ROOT}/handoffs/${encodeURIComponent(handoffId)}/respond`, {
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ target, feedback }),
      })
      return readJson(response)
    }
  }
}
