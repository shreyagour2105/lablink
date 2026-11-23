from django.core.management.base import BaseCommand

from portal.store import get_store


class Command(BaseCommand):
    help = "Upsert clearly labeled sample equipment and lab metadata without creating Firebase users."

    def handle(self, *args, **options):
        store = get_store()
        labs = [
            ("demo-fabrication", "Sample Fabrication Lab", "Demo Mechanical Engineering"),
            ("demo-electronics", "Sample Electronics Lab", "Demo Electrical Engineering"),
            ("demo-computing", "Sample Computing Lab", "Demo Computer Science"),
        ]
        resources = [
            ("demo-3d-printer", "FDM 3D Printer", "Prototype fabrication", "A sample fused-deposition printer for prototype parts."),
            ("demo-microcontroller-kit", "Microcontroller Development Kit", "Embedded systems", "A sample board and basic sensors for embedded development."),
            ("demo-gpu-workstation", "GPU Workstation", "GPU computing", "A sample shared workstation for GPU workloads."),
        ]
        windows = [{"weekday": day, "opens_at": "09:00", "closes_at": "17:00"} for day in range(5)]
        batch = store.client.batch()
        for (lab_id, lab_name, department), (slug, name, capability, description) in zip(labs, resources, strict=True):
            lab = {"name": lab_name, "department": department, "location": "Demo location", "time_zone": "Asia/Kolkata", "sample": True}
            batch.set(store.collection("labs").document(lab_id), lab, merge=True)
            batch.set(store.collection("equipment").document(slug), {
                "name": name, "capability": capability, "description": description,
                "lab_id": lab_id, "lab_name": lab_name, "department": department,
                "location": lab["location"], "time_zone": lab["time_zone"],
                "sample": True, "active": True, "windows": windows,
            }, merge=True)
        batch.commit()
        self.stdout.write(self.style.SUCCESS("Saved three demo sample equipment records."))
