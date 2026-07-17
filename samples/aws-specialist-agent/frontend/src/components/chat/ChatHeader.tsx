import { Button } from "@/components/ui/button"
import { User } from "lucide-react"
import { useAuth } from "@/hooks/useAuth"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"

type ChatHeaderProps = {
  title?: string | undefined
}

export function ChatHeader({ title }: ChatHeaderProps) {
  const { isAuthenticated, signOut, user } = useAuth()

  // The signed-in user's display name. Prefer the preferred_username claim
  // (set at user creation); never show the full email — for users without a
  // preferred_username, fall back to the email local part (before the @).
  const profile = user?.profile as { email?: string; preferred_username?: string } | undefined
  const displayName = profile?.preferred_username || profile?.email?.split("@")[0]

  return (
    <header className="flex items-center justify-between p-4 border-b w-full">
      <div className="flex items-center">
        <h1 className="text-xl font-bold">{title || "AgentCore AWS Specialist Agent"}</h1>
      </div>
      <div className="flex items-center gap-3">
        {isAuthenticated && displayName && (
          <span className="flex items-center gap-1.5 text-sm text-gray-600">
            <User className="h-4 w-4" />
            <span className="max-w-[220px] truncate" title={displayName}>
              {displayName}
            </span>
          </span>
        )}
        {isAuthenticated && (
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="outline">Logout</Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Confirm Logout</AlertDialogTitle>
                <AlertDialogDescription>
                  Are you sure you want to log out? You will need to sign in again to access your
                  account.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={() => signOut()}>Confirm</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        )}
      </div>
    </header>
  )
}
