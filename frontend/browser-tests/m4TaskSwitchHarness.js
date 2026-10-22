import { createApp, h, ref } from 'vue'
import Panel from '../src/components/OutputPackagePanel.vue'
const task = ref('task-a')
window.switchTask = value => { task.value = value }
const api = {
 getTaskOutputPackageOptions: async id => ({ task_id:id, production_id:'11111111-1111-4111-8111-111111111111', storage_root_id:'root', output_types:[{id:'ot',name:'Final'}] }),
 listTaskOutputPackages: async () => [],
 listTaskStorageObjects: async id => ({ task_id:id,parent_id:'root',items:[] }),
 getPersonalGoogle: async () => ({ allowed:true,linked:true }),
 getPersonalPickerConfig: async () => ({ client_id:'cid',api_key:'key',app_id:'123',scope:'openid https://www.googleapis.com/auth/drive.file',subject_sha256:window.linkedHash })
}
createApp({ render:()=>h(Panel,{taskId:task.value,api}) }).mount('#fixture')
