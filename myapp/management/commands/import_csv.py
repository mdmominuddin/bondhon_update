import csv
from datetime import datetime
from django.core.management.base import BaseCommand
from myapp.models import SocietyMember


class Command(BaseCommand):
    help = 'Import society members from a CSV file into the database.'

    def add_arguments(self, parser):
        parser.add_argument(
            'csv_file', type=str, help='Path to the CSV file to import.'
        )

    def handle(self, *args, **options):
        # Retrieve the CSV file path provided as an argument
        csv_file_path = options['csv_file']

        try:
            # Open the CSV file
            with open(csv_file_path, 'r') as csvfile:
                reader = csv.DictReader(csvfile)

                # Iterate through each row in the CSV
                for row in reader:
                    try:
                        # Create and save the SocietyMember object
                        member = SocietyMember(
                            name=row['name'],
                            society_designation=row['society_designation'],
                            phone_number=row['phone_number'],
                            address=row.get('address', ''),  # Use default if missing
                            shares=int(row['shares']),
                            joining_date=datetime.strptime(
                                row['joining_date'], '%Y-%m-%d'
                            ).date(),
                        )
                        member.save()
                        self.stdout.write(self.style.SUCCESS(f"Added: {member.name}"))
                    except Exception as e:
                        self.stderr.write(
                            self.style.ERROR(
                                f"Error adding row {row.get('name', 'unknown')}: {str(e)}"
                            )
                        )

        except FileNotFoundError:
            self.stderr.write(self.style.ERROR(f"File not found: {csv_file_path}"))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"An error occurred: {str(e)}"))

        self.stdout.write(self.style.SUCCESS("CSV import completed!"))
