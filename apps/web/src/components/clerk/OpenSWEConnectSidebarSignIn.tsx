import { UserButton, useAuth } from "@clerk/react";
import { LogInIcon, ServerIcon, SmartphoneIcon } from "lucide-react";

import { hasCloudPublicConfig } from "../../cloud/publicConfig";
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem } from "../ui/sidebar";
import { MobileClientsUserProfilePage } from "./MobileClientsUserProfilePage";
import { OpenSWEConnectUserProfilePage } from "./OpenSWEConnectUserProfilePage";
import { useT3ConnectAuthPrompt } from "./useT3ConnectAuthPrompt";

export function OpenSWEConnectSidebarSignIn() {
  if (!hasCloudPublicConfig()) return null;

  return <ConfiguredOpenSWEConnectSidebarSignIn />;
}

export function OpenSWEConnectSidebarAvatar() {
  if (!hasCloudPublicConfig()) return null;

  return <ConfiguredOpenSWEConnectSidebarAvatar />;
}

function ConfiguredOpenSWEConnectSidebarAvatar() {
  const { isLoaded, isSignedIn } = useAuth();

  if (!isLoaded || !isSignedIn) return null;

  return (
    <UserButton
      appearance={{
        elements: {
          avatarBox: "size-7",
          userButtonTrigger: "rounded-lg p-1 hover:bg-sidebar-row-hover",
        },
      }}
    >
      <UserButton.UserProfilePage
        label="Mobile clients"
        labelIcon={<SmartphoneIcon className="size-4" />}
        url="mobile-clients"
      >
        <MobileClientsUserProfilePage />
      </UserButton.UserProfilePage>
      <UserButton.UserProfilePage
        label="Open SWE Connect"
        labelIcon={<ServerIcon className="size-4" />}
        url="openswe-connect"
      >
        <OpenSWEConnectUserProfilePage />
      </UserButton.UserProfilePage>
    </UserButton>
  );
}

function ConfiguredOpenSWEConnectSidebarSignIn() {
  const { isLoaded, isSignedIn } = useAuth();
  const { authPrompt, openAuthPrompt } = useT3ConnectAuthPrompt();

  if (!isLoaded || isSignedIn) return null;

  return (
    <>
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton onClick={openAuthPrompt}>
            <LogInIcon />
            <span>Sign in to Open SWE Connect</span>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
      {authPrompt}
    </>
  );
}
