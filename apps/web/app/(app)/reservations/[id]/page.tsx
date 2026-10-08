import type { Metadata } from "next";

import { ReservationDetail } from "@/features/reservations/reservation-detail";

export const metadata: Metadata = { title: "Reservation" };

export default async function ReservationPage(props: PageProps<"/reservations/[id]">) {
  const { id } = await props.params;
  return <ReservationDetail id={id} />;
}
