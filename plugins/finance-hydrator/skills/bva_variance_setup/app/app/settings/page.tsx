import { SettingsWizard } from "@/components/settings-wizard"
import { AdminPanel } from "@/components/admin-panel"

export default function SettingsPage() {
  return (
    <>
      <div className="max-w-[1400px] mx-auto pt-6 px-4">
        <AdminPanel />
      </div>
      <SettingsWizard />
    </>
  )
}
