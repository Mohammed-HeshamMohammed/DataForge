"""Generates workers/scraping/src/dataforge_scraping/data/schemaorg_mappings.json, the schema.org -> canonical field
mapping used by structured-data extraction and record details. Edit the tables here, then run from the repository root:

    python scripts/schemaorg-mappings.py

Shared shapes (addresses, geo coordinates, offers, ratings, contacts) are written once, so every type reads them the
same way. A subtype may be listed under only one type.
"""

import json
import sys
from pathlib import Path


def join(*paths: str, separator: str = ", ") -> dict:
    spec = {"paths": list(paths), "join": True}
    if separator != ", ":
        spec["separator"] = separator
    return spec


def address(prefix: str = "address") -> dict:
    return {
        "address": f"{prefix}.streetAddress",
        "city": f"{prefix}.addressLocality",
        "region": f"{prefix}.addressRegion",
        "postal_code": f"{prefix}.postalCode",
        "country": f"{prefix}.addressCountry",
    }


def geo(prefix: str = "geo") -> dict:
    return {"latitude": f"{prefix}.latitude", "longitude": f"{prefix}.longitude"}


def offer(prefix: str = "offers") -> dict:
    return {
        "price": [f"{prefix}.price", f"{prefix}.lowPrice", f"{prefix}.priceSpecification.price"],
        "price_currency": [f"{prefix}.priceCurrency", f"{prefix}.priceSpecification.priceCurrency"],
        "low_price": f"{prefix}.lowPrice",
        "high_price": f"{prefix}.highPrice",
        "offer_count": f"{prefix}.offerCount",
        "availability": f"{prefix}.availability",
        "item_condition": [f"{prefix}.itemCondition", "itemCondition"],
        "seller": f"{prefix}.seller.name",
        "price_valid_until": f"{prefix}.priceValidUntil",
        "offer_url": f"{prefix}.url",
    }


def rating(prefix: str = "aggregateRating") -> dict:
    return {
        "rating": f"{prefix}.ratingValue",
        "best_rating": f"{prefix}.bestRating",
        "worst_rating": f"{prefix}.worstRating",
        "review_count": [f"{prefix}.reviewCount", f"{prefix}.ratingCount"],
    }


def contact() -> dict:
    return {
        "phone": ["telephone", "contactPoint.telephone"],
        "email": ["email", "contactPoint.email"],
        "fax": "faxNumber",
    }


def base(title_key: str = "name") -> dict:
    return {
        title_key: "name",
        "alternate_name": "alternateName",
        "description": "description",
        "url": ["url", "mainEntityOfPage"],
        "image": "image",
        "identifier": ["identifier.value", "identifier"],
    }


BUSINESS = {
    **base(),
    "legal_name": "legalName",
    **contact(),
    **address(),
    **geo(),
    "opening_hours": join("openingHours"),
    "opening_hours_specification": join("openingHoursSpecification.dayOfWeek"),
    "price_range": "priceRange",
    "currencies_accepted": "currenciesAccepted",
    "payment_accepted": "paymentAccepted",
    **rating(),
    "logo": "logo",
    "same_as": join("sameAs"),
    "has_map": "hasMap",
    "area_served": join("areaServed"),
    "vat_id": "vatID",
    "tax_id": "taxID",
    "founding_date": "foundingDate",
    "parent_organization": "parentOrganization.name",
    "brand": "brand.name",
    "serves_cuisine": join("servesCuisine"),
    "menu": ["hasMenu", "menu"],
    "accepts_reservations": "acceptsReservations",
    "amenities": join("amenityFeature.name"),
    "keywords": join("keywords"),
}

LOCAL_BUSINESS_SUBTYPES = [
    "AccountingService", "AnimalShelter", "Attorney", "AutoBodyShop", "AutoDealer", "AutoPartsStore", "AutoRental", "AutoRepair",
    "AutoWash", "AutomatedTeller", "AutomotiveBusiness", "Bakery", "BankOrCreditUnion", "BarOrPub", "BeautySalon", "BikeStore",
    "BookStore", "Brewery", "BrewPub", "CafeOrCoffeeShop", "ChildCare", "ClothingStore", "ComputerStore", "ConvenienceStore",
    "DaySpa", "Dentist", "DepartmentStore", "Distillery", "DryCleaningOrLaundry", "Electrician", "ElectronicsStore", "EmergencyService",
    "EmploymentAgency", "EntertainmentBusiness", "ExerciseGym", "FastFoodRestaurant", "FinancialService", "Florist", "FoodEstablishment",
    "FurnitureStore", "GardenStore", "GasStation", "GeneralContractor", "GolfCourse", "GovernmentOffice", "GroceryStore", "HairSalon",
    "HardwareStore", "HealthAndBeautyBusiness", "HealthClub", "HobbyShop", "HomeAndConstructionBusiness", "HomeGoodsStore", "HousePainter",
    "HVACBusiness", "IceCreamShop", "InsuranceAgency", "InternetCafe", "JewelryStore", "LegalService", "Library", "LiquorStore",
    "Locksmith", "MedicalBusiness", "MedicalClinic", "MensClothingStore", "MobilePhoneStore", "MotorcycleDealer", "MotorcycleRepair",
    "MovieRental", "MovieTheater", "MovingCompany", "MusicStore", "NailSalon", "NightClub", "Notary", "OfficeEquipmentStore",
    "Optician", "OutletStore", "PawnShop", "PetStore", "Pharmacy", "Physician", "Plumber", "ProfessionalService", "RealEstateAgent",
    "RecyclingCenter", "Restaurant", "RoofingContractor", "SelfStorage", "ShoeStore", "ShoppingCenter", "SkiResort", "SportingGoodsStore",
    "SportsActivityLocation", "SportsClub", "Store", "TattooParlor", "TennisComplex", "TireShop", "TouristInformationCenter", "ToyStore",
    "TravelAgency", "VeterinaryCare", "WholesaleStore", "Winery", "Dermatology", "Hospital", "DiagnosticLab", "Bowling", "BowlingAlley",
    "AmusementPark", "ArtGallery", "Casino", "ComedyClub", "PublicSwimmingPool", "StadiumOrArena",
]

LODGING_SUBTYPES = ["BedAndBreakfast", "Campground", "Hostel", "Hotel", "Motel", "Resort", "VacationRental"]

ORGANIZATION_SUBTYPES = [
    "Airline", "CollegeOrUniversity", "Consortium", "Corporation", "EducationalOrganization", "ElementarySchool", "FundingScheme",
    "GovernmentOrganization", "HighSchool", "LibrarySystem", "MedicalOrganization", "MiddleSchool", "MusicGroup", "NGO",
    "NewsMediaOrganization", "OnlineBusiness", "OnlineStore", "PerformingGroup", "PoliticalParty", "Preschool", "Project",
    "ResearchOrganization", "ResearchProject", "School", "SearchRescueOrganization", "SportsOrganization", "SportsTeam",
    "TheaterGroup", "WorkersUnion", "DanceGroup", "FundingAgency",
]

ARTICLE_SUBTYPES = [
    "AdvertiserContentArticle", "AnalysisNewsArticle", "APIReference", "AskPublicNewsArticle", "BackgroundNewsArticle", "BlogPosting",
    "DiscussionForumPosting", "LiveBlogPosting", "MedicalScholarlyArticle", "NewsArticle", "OpinionNewsArticle", "Report",
    "ReportageNewsArticle", "ReviewNewsArticle", "SatiricalArticle", "ScholarlyArticle", "SocialMediaPosting", "TechArticle",
]

EVENT_SUBTYPES = [
    "BusinessEvent", "ChildrensEvent", "ComedyEvent", "CourseInstance", "DanceEvent", "DeliveryEvent", "EducationEvent", "EventSeries",
    "ExhibitionEvent", "Festival", "FoodEvent", "Hackathon", "LiteraryEvent", "MusicEvent", "PublicationEvent", "SaleEvent",
    "ScreeningEvent", "SocialEvent", "SportsEvent", "TheaterEvent", "VisualArtsEvent",
]

PLACE_SUBTYPES = [  # Accommodation is its own mapped type
    "AdministrativeArea", "Airport", "Aquarium", "Beach", "BoatTerminal", "Bridge", "BusStation", "BusStop", "Cemetery",
    "Church", "City", "CivicStructure", "Country", "Courthouse", "Crematorium", "EventVenue", "FireStation", "LandmarksOrHistoricalBuildings",
    "Landform", "Mosque", "Mountain", "Museum", "MusicVenue", "Park", "ParkingFacility", "PerformingArtsTheater", "PlaceOfWorship",
    "Playground", "PoliceStation", "RVPark", "State", "SubwayStation", "Synagogue", "TaxiStand", "TouristAttraction",
    "TouristDestination", "TrainStation", "Zoo", "BuddhistTemple", "HinduTemple", "GatedResidenceCommunity",
]

ACCOMMODATION_SUBTYPES = ["Apartment", "CampingPitch", "House", "HotelRoom", "Room", "SingleFamilyResidence", "Suite", "MeetingRoom"]

TYPES = {
    "Product": {
        "canonical_entity": "product",
        "includes_subtypes": ["ProductGroup", "ProductModel", "IndividualProduct", "SomeProducts", "DietarySupplement", "Drug"],
        "fields": {
            **base(),
            "sku": "sku",
            "gtin": ["gtin", "gtin13", "gtin12", "gtin14", "gtin8"],
            "mpn": "mpn",
            "product_id": "productID",
            "brand": "brand.name",
            "manufacturer": "manufacturer.name",
            "model": "model",
            "category": "category",
            "color": "color",
            "material": "material",
            "size": "size",
            "pattern": "pattern",
            "audience": "audience.name",
            "weight": "weight.value",
            "weight_unit": "weight.unitCode",
            "width": "width.value",
            "height": "height.value",
            "depth": "depth.value",
            **offer(),
            **rating(),
            "images": join("image"),
            "keywords": join("keywords"),
            "release_date": "releaseDate",
            "country_of_origin": "countryOfOrigin",
            "is_variant_of": "isVariantOf.name",
            "additional_properties": join("additionalProperty.name"),
            "shipping_cost": "offers.shippingDetails.shippingRate.value",
            "return_days": "offers.hasMerchantReturnPolicy.merchantReturnDays",
        },
    },
    "Vehicle": {
        "canonical_entity": "product",
        "includes_subtypes": ["Car", "Motorcycle", "BusOrCoach", "MotorizedBicycle"],
        "fields": {
            **base(),
            "brand": ["brand.name", "manufacturer.name"],
            "model": "model",
            "vehicle_model_date": ["vehicleModelDate", "modelDate"],
            "production_date": "productionDate",
            "vin": "vehicleIdentificationNumber",
            "mileage": "mileageFromOdometer.value",
            "mileage_unit": "mileageFromOdometer.unitCode",
            "fuel_type": "fuelType",
            "transmission": "vehicleTransmission",
            "body_type": "bodyType",
            "drive": "driveWheelConfiguration",
            "color": "color",
            "interior_color": "vehicleInteriorColor",
            "engine_displacement": "vehicleEngine.engineDisplacement.value",
            "engine_power": "vehicleEngine.enginePower.value",
            "seats": "seatingCapacity",
            "doors": "numberOfDoors",
            "owners": "numberOfPreviousOwners",
            **offer(),
            **rating(),
        },
    },
    "Offer": {
        "canonical_entity": "product",
        "includes_subtypes": ["AggregateOffer"],
        "fields": {
            "name": ["name", "itemOffered.name"],
            "description": "description",
            "url": "url",
            "image": ["image", "itemOffered.image"],
            "sku": ["sku", "itemOffered.sku"],
            "gtin": ["gtin", "gtin13", "itemOffered.gtin13", "itemOffered.gtin"],
            "price": ["price", "lowPrice", "priceSpecification.price"],
            "price_currency": ["priceCurrency", "priceSpecification.priceCurrency"],
            "low_price": "lowPrice",
            "high_price": "highPrice",
            "offer_count": "offerCount",
            "availability": "availability",
            "item_condition": "itemCondition",
            "seller": "seller.name",
            "valid_from": "validFrom",
            "valid_through": ["validThrough", "priceValidUntil"],
            "category": "category",
        },
    },
    "LocalBusiness": {
        "canonical_entity": "business",
        "includes_subtypes": LOCAL_BUSINESS_SUBTYPES,
        "fields": BUSINESS,
    },
    "LodgingBusiness": {
        "canonical_entity": "business",
        "includes_subtypes": LODGING_SUBTYPES,
        "fields": {
            **BUSINESS,
            "star_rating": "starRating.ratingValue",
            "checkin_time": "checkinTime",
            "checkout_time": "checkoutTime",
            "number_of_rooms": "numberOfRooms",
            "pets_allowed": "petsAllowed",
            "available_languages": join("availableLanguage"),
        },
    },
    "Organization": {
        "canonical_entity": "business",
        "includes_subtypes": ORGANIZATION_SUBTYPES,
        "fields": {
            **base(),
            "legal_name": "legalName",
            **contact(),
            **address(),
            "logo": "logo",
            "founding_date": "foundingDate",
            "founder": join("founder.name"),
            "number_of_employees": "numberOfEmployees.value",
            "same_as": join("sameAs"),
            "duns": "duns",
            "lei": "leiCode",
            "vat_id": "vatID",
            "tax_id": "taxID",
            "naics": "naics",
            "isic": "isicV4",
            "slogan": "slogan",
            "parent_organization": "parentOrganization.name",
            "area_served": join("areaServed"),
            "knows_about": join("knowsAbout"),
            **rating(),
        },
    },
    "Person": {
        "canonical_entity": "person",
        "includes_subtypes": ["Patient"],
        "fields": {
            **base(),
            "given_name": "givenName",
            "family_name": "familyName",
            "honorific_prefix": "honorificPrefix",
            "job_title": "jobTitle",
            "works_for": "worksFor.name",
            "affiliation": "affiliation.name",
            "alumni_of": join("alumniOf.name"),
            **contact(),
            **address(),
            "same_as": join("sameAs"),
            "knows_about": join("knowsAbout"),
            "nationality": "nationality.name",
        },
    },
    "JobPosting": {
        "canonical_entity": "job",
        "includes_subtypes": [],
        "fields": {
            "title": ["title", "name"],
            "description": "description",
            "url": ["url", "mainEntityOfPage"],
            "identifier": ["identifier.value", "identifier"],
            "date_posted": "datePosted",
            "valid_through": "validThrough",
            "employment_type": join("employmentType"),
            "hiring_organization": "hiringOrganization.name",
            "hiring_organization_url": ["hiringOrganization.sameAs", "hiringOrganization.url"],
            "hiring_organization_logo": "hiringOrganization.logo",
            "address": "jobLocation.address.streetAddress",
            "city": "jobLocation.address.addressLocality",
            "region": "jobLocation.address.addressRegion",
            "postal_code": "jobLocation.address.postalCode",
            "country": "jobLocation.address.addressCountry",
            "latitude": "jobLocation.geo.latitude",
            "longitude": "jobLocation.geo.longitude",
            "job_location_type": "jobLocationType",
            "applicant_location": join("applicantLocationRequirements.name"),
            "salary_currency": ["baseSalary.currency", "estimatedSalary.currency"],
            "salary": ["baseSalary.value.value", "estimatedSalary.value.value"],
            "salary_min": ["baseSalary.value.minValue", "estimatedSalary.value.minValue"],
            "salary_max": ["baseSalary.value.maxValue", "estimatedSalary.value.maxValue"],
            "salary_unit": ["baseSalary.value.unitText", "estimatedSalary.value.unitText"],
            "industry": join("industry"),
            "occupational_category": join("occupationalCategory"),
            "education_requirements": join("educationRequirements.credentialCategory", "educationRequirements"),
            "experience_requirements": ["experienceRequirements.monthsOfExperience", "experienceRequirements"],
            "qualifications": "qualifications",
            "skills": join("skills"),
            "responsibilities": "responsibilities",
            "job_benefits": "jobBenefits",
            "work_hours": "workHours",
            "total_job_openings": "totalJobOpenings",
            "direct_apply": "directApply",
            "job_start_date": "jobStartDate",
            "immediate_start": "jobImmediateStart",
        },
    },
    "RealEstateListing": {
        "canonical_entity": "listing",
        "includes_subtypes": [],
        "fields": {
            **base(),
            "date_posted": "datePosted",
            "lease_length": "leaseLength.value",
            **offer(),
            "address": ["about.address.streetAddress", "mainEntity.address.streetAddress", "address.streetAddress"],
            "city": ["about.address.addressLocality", "mainEntity.address.addressLocality", "address.addressLocality"],
            "region": ["about.address.addressRegion", "mainEntity.address.addressRegion", "address.addressRegion"],
            "postal_code": ["about.address.postalCode", "mainEntity.address.postalCode", "address.postalCode"],
            "floor_size": ["about.floorSize.value", "mainEntity.floorSize.value", "floorSize.value"],
            "bedrooms": ["about.numberOfBedrooms", "mainEntity.numberOfBedrooms", "numberOfBedrooms"],
            "bathrooms": ["about.numberOfBathroomsTotal", "mainEntity.numberOfBathroomsTotal", "numberOfBathroomsTotal"],
            "rooms": ["about.numberOfRooms", "mainEntity.numberOfRooms", "numberOfRooms"],
        },
    },
    "Accommodation": {
        "canonical_entity": "listing",
        "includes_subtypes": ACCOMMODATION_SUBTYPES,
        "fields": {
            **base(),
            **address(),
            **geo(),
            "floor_size": "floorSize.value",
            "floor_size_unit": "floorSize.unitCode",
            "rooms": "numberOfRooms",
            "bedrooms": "numberOfBedrooms",
            "bathrooms": ["numberOfBathroomsTotal", "numberOfFullBathrooms"],
            "year_built": "yearBuilt",
            "occupancy": ["occupancy.value", "occupancy.maxValue"],
            "pets_allowed": "petsAllowed",
            "permitted_usage": "permittedUsage",
            "accommodation_category": "accommodationCategory",
            "amenities": join("amenityFeature.name"),
            "tour_booking_page": "tourBookingPage",
            **offer(),
        },
    },
    "Event": {
        "canonical_entity": "event",
        "includes_subtypes": EVENT_SUBTYPES,
        "fields": {
            **base(),
            "start_date": "startDate",
            "end_date": "endDate",
            "door_time": "doorTime",
            "duration": "duration",
            "event_status": "eventStatus",
            "attendance_mode": "eventAttendanceMode",
            "venue": "location.name",
            "address": "location.address.streetAddress",
            "city": "location.address.addressLocality",
            "region": "location.address.addressRegion",
            "postal_code": "location.address.postalCode",
            "country": "location.address.addressCountry",
            "latitude": "location.geo.latitude",
            "longitude": "location.geo.longitude",
            "online_url": "location.url",
            "organizer": "organizer.name",
            "organizer_url": "organizer.url",
            "performer": join("performer.name"),
            **offer(),
            "ticket_url": "offers.url",
            "valid_from": "offers.validFrom",
            "language": "inLanguage",
            "audience": "audience.name",
            "maximum_attendees": "maximumAttendeeCapacity",
            "remaining_attendees": "remainingAttendeeCapacity",
            "is_free": "isAccessibleForFree",
            "keywords": join("keywords"),
        },
    },
    "Article": {
        "canonical_entity": "article",
        "includes_subtypes": ARTICLE_SUBTYPES,
        "fields": {
            "title": ["headline", "name"],
            "alternative_headline": "alternativeHeadline",
            "description": "description",
            "url": ["url", "mainEntityOfPage"],
            "image": "image",
            "author": join("author.name"),
            "author_url": "author.url",
            "publisher": "publisher.name",
            "publisher_logo": "publisher.logo",
            "date_published": "datePublished",
            "date_modified": "dateModified",
            "date_created": "dateCreated",
            "section": join("articleSection"),
            "keywords": join("keywords"),
            "about": join("about.name"),
            "word_count": "wordCount",
            "language": "inLanguage",
            "is_accessible_for_free": "isAccessibleForFree",
            "comment_count": "commentCount",
            "thumbnail": "thumbnailUrl",
            "citation_count": "citation",
            "article_body": "articleBody",
            "identifier": ["identifier.value", "identifier"],
        },
    },
    "Book": {
        "canonical_entity": "book",
        "includes_subtypes": ["Audiobook"],
        "fields": {
            **base(),
            "author": join("author.name"),
            "isbn": ["isbn", "workExample.isbn"],
            "number_of_pages": ["numberOfPages", "workExample.numberOfPages"],
            "book_format": ["bookFormat", "workExample.bookFormat"],
            "book_edition": ["bookEdition", "workExample.bookEdition"],
            "publisher": "publisher.name",
            "date_published": ["datePublished", "workExample.datePublished"],
            "language": "inLanguage",
            "genre": join("genre"),
            "illustrator": join("illustrator.name"),
            "translator": join("translator.name"),
            "series": "isPartOf.name",
            "awards": join("award"),
            **offer(),
            **rating(),
        },
    },
    "Recipe": {
        "canonical_entity": "recipe",
        "includes_subtypes": [],
        "fields": {
            **base(),
            "author": join("author.name"),
            "date_published": "datePublished",
            "prep_time": "prepTime",
            "cook_time": "cookTime",
            "total_time": "totalTime",
            "recipe_yield": "recipeYield",
            "recipe_category": join("recipeCategory"),
            "recipe_cuisine": join("recipeCuisine"),
            "suitable_for_diet": join("suitableForDiet"),
            "calories": "nutrition.calories",
            "fat": "nutrition.fatContent",
            "protein": "nutrition.proteinContent",
            "carbohydrates": "nutrition.carbohydrateContent",
            "ingredients": join("recipeIngredient", "ingredients", separator="; "),
            "instructions": join("recipeInstructions.text", "recipeInstructions.itemListElement.text", "recipeInstructions", separator=" | "),
            "keywords": join("keywords"),
            "video": ["video.contentUrl", "video.embedUrl"],
            **rating(),
        },
    },
    "Movie": {
        "canonical_entity": "creative_work",
        "includes_subtypes": ["TVSeries", "TVSeason", "TVEpisode", "MovieSeries", "CreativeWorkSeason"],
        "fields": {
            **base(),
            "date_published": ["datePublished", "startDate"],
            "director": join("director.name"),
            "actors": join("actor.name"),
            "creator": join("creator.name"),
            "genre": join("genre"),
            "duration": "duration",
            "content_rating": "contentRating",
            "production_company": join("productionCompany.name"),
            "country_of_origin": "countryOfOrigin.name",
            "language": "inLanguage",
            "number_of_seasons": "numberOfSeasons",
            "number_of_episodes": "numberOfEpisodes",
            "trailer": ["trailer.embedUrl", "trailer.contentUrl"],
            **rating(),
        },
    },
    "Course": {
        "canonical_entity": "course",
        "includes_subtypes": [],
        "fields": {
            **base(),
            "course_code": "courseCode",
            "provider": "provider.name",
            "provider_url": ["provider.sameAs", "provider.url"],
            "educational_level": "educationalLevel",
            "language": "inLanguage",
            "course_mode": join("hasCourseInstance.courseMode"),
            "course_workload": "hasCourseInstance.courseWorkload",
            "credential": "educationalCredentialAwarded",
            "teaches": join("teaches"),
            "prerequisites": join("coursePrerequisites"),
            **offer(),
            **rating(),
        },
    },
    "SoftwareApplication": {
        "canonical_entity": "software",
        "includes_subtypes": ["MobileApplication", "WebApplication", "VideoGame", "SoftwareSourceCode"],
        "fields": {
            **base(),
            "application_category": "applicationCategory",
            "operating_system": join("operatingSystem"),
            "software_version": "softwareVersion",
            "file_size": "fileSize",
            "download_url": "downloadUrl",
            "install_url": "installUrl",
            "release_notes": "releaseNotes",
            "date_published": "datePublished",
            "date_modified": "dateModified",
            "author": join("author.name"),
            "publisher": "publisher.name",
            "license": "license",
            "programming_language": join("programmingLanguage.name", "programmingLanguage"),
            "code_repository": "codeRepository",
            "content_rating": "contentRating",
            "screenshot": "screenshot",
            **offer(),
            **rating(),
        },
    },
    "Review": {
        "canonical_entity": "review",
        "includes_subtypes": ["CriticReview", "UserReview", "EmployerReview", "ClaimReview", "Recommendation"],
        "fields": {
            "title": ["name", "headline"],
            "url": "url",
            "item_reviewed": "itemReviewed.name",
            "item_reviewed_type": "itemReviewed.@type",
            "rating": "reviewRating.ratingValue",
            "best_rating": "reviewRating.bestRating",
            "worst_rating": "reviewRating.worstRating",
            "author": join("author.name"),
            "publisher": "publisher.name",
            "date_published": "datePublished",
            "review_body": "reviewBody",
            "positive_notes": join("positiveNotes.itemListElement.name"),
            "negative_notes": join("negativeNotes.itemListElement.name"),
            "language": "inLanguage",
        },
    },
    "VideoObject": {
        "canonical_entity": "media",
        "includes_subtypes": ["Clip", "MusicVideoObject"],
        "fields": {
            **base(),
            "thumbnail": "thumbnailUrl",
            "upload_date": "uploadDate",
            "duration": "duration",
            "content_url": "contentUrl",
            "embed_url": "embedUrl",
            "views": "interactionStatistic.userInteractionCount",
            "author": join("author.name"),
            "publisher": "publisher.name",
            "language": "inLanguage",
            "is_family_friendly": "isFamilyFriendly",
            "expires": "expires",
            "transcript": "transcript",
        },
    },
    "Dataset": {
        "canonical_entity": "dataset",
        "includes_subtypes": ["DataFeed"],
        "fields": {
            **base(),
            "license": "license",
            "creator": join("creator.name"),
            "publisher": "publisher.name",
            "date_published": "datePublished",
            "date_modified": "dateModified",
            "version": "version",
            "keywords": join("keywords"),
            "temporal_coverage": "temporalCoverage",
            "spatial_coverage": "spatialCoverage.name",
            "variables_measured": join("variableMeasured.name", "variableMeasured"),
            "distribution_url": join("distribution.contentUrl"),
            "distribution_format": join("distribution.encodingFormat"),
            "catalog": "includedInDataCatalog.name",
            "is_accessible_for_free": "isAccessibleForFree",
        },
    },
    "HowTo": {
        "canonical_entity": "creative_work",
        "includes_subtypes": [],
        "fields": {
            **base(),
            "total_time": "totalTime",
            "estimated_cost": ["estimatedCost.value", "estimatedCost"],
            "estimated_cost_currency": "estimatedCost.currency",
            "supplies": join("supply.name", "supply"),
            "tools": join("tool.name", "tool"),
            "steps": join("step.text", "step.name", "step.itemListElement.text", separator=" | "),
        },
    },
    "Question": {
        "canonical_entity": "question",
        "includes_subtypes": [],
        "fields": {
            "question": ["name", "text"],
            "answer": join("acceptedAnswer.text", "suggestedAnswer.text", separator=" | "),
            "answer_count": "answerCount",
            "upvote_count": "upvoteCount",
            "date_created": "dateCreated",
            "author": "author.name",
            "url": "url",
        },
    },
    "Place": {
        "canonical_entity": "place",
        "includes_subtypes": PLACE_SUBTYPES,
        "fields": {
            **base(),
            **contact(),
            **address(),
            **geo(),
            "opening_hours": join("openingHours"),
            "public_access": "publicAccess",
            "is_free": "isAccessibleForFree",
            "maximum_attendees": "maximumAttendeeCapacity",
            "same_as": join("sameAs"),
            "has_map": "hasMap",
            **rating(),
        },
    },
}

seen: dict[str, str] = {}
for name, spec in TYPES.items():
    for sub in spec["includes_subtypes"]:
        if sub in seen or sub in TYPES:
            sys.exit(f"subtype {sub} listed twice ({seen.get(sub)} and {name})")
        seen[sub] = name

document = {  # priority: which entity describes a detail page when several are present
    "version": 2,
    "description": (
        "schema.org type -> DataForge canonical record fields. A field spec is a dotted path, a list of fallback paths "
        "(first non-empty wins), or {\"paths\": [...], \"join\": true} to join every value found. Types are matched by "
        "name or through includes_subtypes; the record keeps the page's own type in schema_type."
    ),
    "priority": [
        "Product", "Vehicle", "JobPosting", "RealEstateListing", "Accommodation", "LodgingBusiness", "LocalBusiness", "Event", "Recipe",
        "Book", "Movie", "Course", "SoftwareApplication", "Dataset", "VideoObject", "HowTo", "Article", "Review", "Place", "Offer",
        "Organization", "Question", "Person",
    ],
    "types": TYPES,
}
target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "workers/scraping/src/dataforge_scraping/data/schemaorg_mappings.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"{len(TYPES)} types, {sum(len(t['includes_subtypes']) for t in TYPES.values())} subtypes, {sum(len(t['fields']) for t in TYPES.values())} fields")
